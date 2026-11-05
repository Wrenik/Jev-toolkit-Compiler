import type { Register } from 'claude-code'

import { MIN_CONTEXT_TOKENS, MODES, PERSON_ORIGINS, describe, pushPrompt, shouldAsk, shouldIngest, toastFor } from './logic'
import type { Advice, Mode } from './logic'

// jev-context: before a prompt is sent, and only when the context is big, ask the local jev_context
// server whether compacting or clearing would pay off. Advice only: it never runs /compact or /clear.
// The server is shared with jev-router (same port). When both mods are installed and ask at the same
// moment, the server sends ONE request to the judge for both; alone, each behaves as if the other
// did not exist.

const BASE = 'http://127.0.0.1:8765'
const HEADERS = { 'X-Jev-Router': '1', 'Content-Type': 'application/json' }
const TIMEOUT_MS = 8000 // advice is not on the prompt's critical path, so it may take longer than routing

// Which Python runs the server: userConfig `python`, else JEV_PYTHON, else `python`/`python3` on PATH.
// jev-toolkit-compiler and jev-toolkit-usage must be pip-installed there.
let pythonOption = ''
let isStarting = false
let recentPrompts: string[] = []
let lastAskedAt: number | null = null
let lastTurnEnd: number | null = null
let lastAdvice: Advice | null = null
let lastSeen = 'no prompt seen since load'
let lastIngestAt: number | null = null
let lastIngest = 'no automatic ingest yet'

async function getMode($: any): Promise<Mode> {
  const mode = await $.store.get('mode')
  return MODES.includes(mode) ? mode : 'advise'
}

// How to start the server: the configured Python, else PATH. Development fallback: when JEV_TOOLKIT points at
// a checkout with a .venv, that venv's Python is used and the server starts inside the checkout.
async function launchOf($: any): Promise<{ python: string; cwd?: string }> {
  const isWindows = (await $.env.get('OS')) === 'Windows_NT'
  const configured = pythonOption || ((await $.env.get('JEV_PYTHON')) ?? '')
  if (configured) return { python: configured }
  const dev = ((await $.env.get('JEV_TOOLKIT')) ?? '').replace(/\\/g, '/').replace(/\/$/, '')
  if (dev) return { python: isWindows ? `${dev}/.venv/Scripts/python.exe` : `${dev}/.venv/bin/python`, cwd: dev }
  return { python: isWindows ? 'python' : 'python3' }
}

async function fetchJson($: any, path: string, init: any, ms: number): Promise<any> {
  const timeout = $.clock.sleep(ms).then(() => null)
  const response = await Promise.race([$.http.fetch(`${BASE}${path}`, { headers: HEADERS, ...init }), timeout])
  if (response === null) throw new Error(`timeout after ${ms} ms`)
  return JSON.parse(response.text)
}

async function health($: any): Promise<any> {
  try {
    return await fetchJson($, '/health', {}, 800)
  } catch {
    return null
  }
}

// Makes sure the shared server is up AND has the context endpoints. If jev-router started its own,
// plainer server first, ask it to exit and start the shared one in its place.
async function ensureServer($: any): Promise<void> {
  if (isStarting) return
  const h = await health($)
  if (h?.ok && h.has_context) return
  isStarting = true
  try {
    // Two attempts: jev-router starts its own server at the same moment. The first context server then loses the
    // port race and exits within seconds; the second attempt sees the router-only server, asks it to exit and
    // takes its place. A server that keeps running (the normal case) ends the loop on its first pass.
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const current = attempt === 0 ? h : await health($)
      if (current?.ok && current.has_context) return
      if (current?.ok) {
        try {
          await fetchJson($, '/shutdown', { method: 'POST', body: '{}' }, 1500)
        } catch {
          // Already going down.
        }
        await $.clock.sleep(500)
      }
      const { python, cwd } = await launchOf($)
      const startedAt = await $.clock.now()
      const child = $.process.spawn({ argv: [python, '-m', 'jev_context', 'serve'], ...(cwd ? { cwd } : {}) })
      for await (const piece of child) {
        if (piece && typeof piece.text === 'string') $.ui.log(`jev-context server: ${piece.text.trim()}`, { to: 'debug' })
      }
      if ((await $.clock.now()) - startedAt > 10_000) return
      await $.clock.sleep(1500)
    }
  } catch (err) {
    $.ui.log(`jev-context: could not start the server (are jev-toolkit-compiler and jev-toolkit-usage installed for this Python?): ${String((err as Error)?.message ?? err)}`)
  } finally {
    isStarting = false
  }
}

// Asks the server to ingest new transcripts in the background. Rate limited here and on the server, never
// throws, never delays a prompt; a failure is only logged (and shown by /jevctx).
async function autoIngest($: any, why: string): Promise<void> {
  try {
    const now = await $.clock.now()
    const enabled = (await $.store.get('autoIngest')) !== 'off'
    if (!shouldIngest({ enabled, now, lastAt: lastIngestAt })) return
    // A server that is still starting ingests by itself when it is up; wait briefly for one that is not.
    let up = !!(await health($))?.ok
    for (let i = 0; !up && i < 10; i += 1) {
      await $.clock.sleep(500)
      up = !!(await health($))?.ok
    }
    if (!up) {
      lastIngest = `${why}: server not reachable`
      return
    }
    lastIngestAt = now
    const r = await fetchJson($, '/ingest', { method: 'POST', body: JSON.stringify({ auto: true }) }, 3000)
    lastIngest = `${why}: ${r.ok ? (r.started ? 'started' : r.reason) : r.error}`
  } catch (err) {
    lastIngest = `${why}: failed (${String((err as Error)?.message ?? err)})`
    $.ui.log(`jev-context: automatic ingest ${lastIngest}`, { to: 'debug' })
  }
}

async function askAdvice($: any, contextTokens: number): Promise<Advice> {
  const now = await $.clock.now()
  const body = JSON.stringify({
    recent_prompts: recentPrompts,
    context_tokens: contextTokens,
    idle_ms: lastTurnEnd === null ? null : now - lastTurnEnd,
  })
  try {
    return await fetchJson($, '/advise', { method: 'POST', body }, TIMEOUT_MS)
  } catch (err) {
    return { ok: false, error: String((err as Error)?.message ?? err) }
  }
}

export const register: Register = (on, options) => {
  pythonOption = String((options as any)?.python ?? '').trim()

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    $.command.register({ name: 'jevctx', description: 'jev-context: status, /jevctx mode off|advise, ingest, autoingest on|off, search <words>' })
    void ensureServer($)
    void autoIngest($, 'session start')
    return started
  })

  // A short, bounded wait: the server answers at once (the ingest itself runs in its own thread).
  on('session.end', async ($, e, next) => {
    await Promise.race([autoIngest($, 'session end'), $.clock.sleep(2500)])
    return next(e)
  })

  on('command.run', { command: 'jevctx' }, async ($, e, next) => {
    const [word, ...rest] = e.args.trim().split(/\s+/)
    if (word === 'mode' && (MODES as readonly string[]).includes(rest[0])) {
      await $.store.set('mode', rest[0])
      return { text: `jev-context mode: ${rest[0]} (applies to every session)` }
    }
    if (word === 'autoingest' && (rest[0] === 'on' || rest[0] === 'off')) {
      await $.store.set('autoIngest', rest[0])
      return { text: `jev-context automatic ingest: ${rest[0]} (applies to every session; /jevctx ingest still works)` }
    }
    if (word === 'ingest') {
      try {
        const r = await fetchJson($, '/ingest', { method: 'POST', body: '{}' }, 60000)
        return { text: r.ok ? `ingested: ${JSON.stringify(r.stats)}` : `ingest failed: ${r.error}` }
      } catch (err) {
        return { text: `ingest failed: ${String((err as Error)?.message ?? err)}` }
      }
    }
    if (word === 'search' && rest.length > 0) {
      try {
        const r = await fetchJson($, '/search', { method: 'POST', body: JSON.stringify({ query: rest.join(' '), limit: 5 }) }, 5000)
        if (!r.ok) return { text: `search failed: ${r.error}` }
        // Results are quotes from past sessions, shown as data.
        return { text: r.hits.length === 0 ? 'no hits' : r.hits.map((h: any) => `${h.id} [${h.role}/${h.kind}] "${h.snippet}"`).join('\n') }
      } catch (err) {
        return { text: `search failed: ${String((err as Error)?.message ?? err)}` }
      }
    }
    const h = await health($)
    return {
      text: [
        `jev-context mode: ${await getMode($)} (change: /jevctx mode off|advise; applies to every session)`,
        `server: ${h?.ok ? `up at ${BASE}, context endpoints ${h.has_context ? 'yes' : 'NO (restarts on the next session start)'}` : `down at ${BASE}`}`,
        `shared judge requests (router + context in one call): ${h?.joint_calls ?? '?'}, separate: ${h?.solo_calls ?? '?'}`,
        `asks only above ${MIN_CONTEXT_TOKENS.toLocaleString()} tokens of context, at most every 5 min`,
        'privacy: the last few prompts are sent to TypeSafe (secrets redacted first); /jevctx mode off stops that',
        `automatic ingest: ${(await $.store.get('autoIngest')) === 'off' ? 'off' : 'on'} (at session start and end, at most every 3 min; /jevctx autoingest on|off); last: ${lastIngest}`,
        `server ingest: ${JSON.stringify(h?.ingest ?? null)}`,
        `last advice: ${describe(lastAdvice)}`,
        `last prompt seen: ${lastSeen}`,
      ].join('\n'),
    }
  })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) lastTurnEnd = await $.clock.now()
    return next(e)
  })

  on('prompt.submit', async ($, e, next) => {
    lastSeen = `origin=${e.origin?.kind ?? 'none'} command=${e.command ? 'yes' : 'no'} meta=${e.isMeta ? 'yes' : 'no'}`
    if (e.command || e.isMeta || !PERSON_ORIGINS.includes(e.origin?.kind)) {
      lastSeen += ' → skipped'
      return next(e)
    }
    recentPrompts = pushPrompt(recentPrompts, e.text)
    const mode = await getMode($)
    const usage = await $.session.usage()
    const now = await $.clock.now()
    const tokens: number | null = usage?.context?.tokens ?? null
    if (!shouldAsk({ mode, contextTokens: tokens, now, lastAskedAt, isSubagent: e.agentId !== undefined })) {
      lastSeen += ' → no question needed'
      return next(e)
    }
    if (!(await health($))?.ok) {
      void ensureServer($)
      return next(e)
    }
    lastAskedAt = now
    lastSeen += ' → asked'
    // Not awaited: the prompt goes on at once. Started BEFORE next(e) so that, if jev-router runs
    // after this hook, its request reaches the server at the same moment and the two share one call.
    void askAdvice($, tokens as number).then((a) => {
      lastAdvice = a
      const text = toastFor(a)
      if (text) $.ui.toast(text)
    })
    return next(e)
  }).catch(($, e, next) => next(e)) // a failure here must never block the prompt: fail open
}
