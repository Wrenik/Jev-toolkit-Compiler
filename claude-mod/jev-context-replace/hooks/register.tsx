import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import { MIN_MESSAGES, MODES, queryOf, replacement, sizeOf, tailStart } from './logic'
import type { Hit, Mode, Msg } from './logic'

// jev-context-replace (EXPERIMENT). When Claude Code compacts the conversation, this answers `session.compact` itself:
// instead of the summary the model would write, the conversation becomes one message of excerpts that the local
// jev-context log returned for the current task, plus the newest turns word for word. It needs the jev-context
// server (127.0.0.1:8765) to be running, e.g. through the jev-context mod, and never replaces anything unless
// the mode is `replace`. On any doubt (server down, no hits, short conversation) it steps aside and the engine
// compacts as usual.

const BASE = 'http://127.0.0.1:8765'
const HEADERS = { 'X-Jev-Router': '1', 'Content-Type': 'application/json' }

let lastReport = 'nothing replaced yet in this session'

const modeAtom = atom({ plugin: 'jev-context-replace', key: 'mode' } as const, 'off' as Mode)
const lastAtom = atom({ plugin: 'jev-context-replace', key: 'last' } as const, '')

// What the person chose (button or command) is kept across sessions and wins; JEV_REPLACE only sets the default.
async function getMode($: any): Promise<Mode> {
  const mode = await $.store.get('mode')
  if (MODES.includes(mode)) return mode
  const forced = await $.env.get('JEV_REPLACE')
  return forced === 'replace' || forced === '1' || forced === 'on' ? 'replace' : 'off'
}

async function setMode($: any, mode: Mode): Promise<void> {
  await $.store.set('mode', mode)
  await update($, modeAtom, () => mode)
  $.ui.toast(mode === 'replace' ? 'Jev context: ON. At the next compaction the log, not a summary, carries the history.' : 'Jev context: OFF. Claude Code compacts as usual.')
}

async function fetchJson($: any, path: string, init: any, ms: number): Promise<any> {
  const timeout = $.clock.sleep(ms).then(() => null)
  const response = await Promise.race([$.http.fetch(`${BASE}${path}`, { headers: HEADERS, ...init }), timeout])
  if (response === null) throw new Error(`timeout after ${ms} ms`)
  return JSON.parse(response.text)
}

export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    const current = await getMode($)
    await update($, modeAtom, () => current)
    $.command.register({ name: 'jevreplace', description: 'Jev context replace: on | off | toggle | now | status' })
    return started
  })

  on('command.run', { command: 'jevreplace' }, async ($, e, next) => {
    const [word, arg] = e.args.trim().split(/\s+/)
    if (word === 'mode' && (MODES as readonly string[]).includes(arg)) {
      await setMode($, arg as Mode)
      return { text: `jev-context-replace mode: ${arg}` }
    }
    if (word === 'on' || word === 'off' || word === 'toggle') {
      const target: Mode = word === 'toggle' ? ((await getMode($)) === 'replace' ? 'off' : 'replace') : word === 'on' ? 'replace' : 'off'
      await setMode($, target)
      return { text: `jev-context-replace: ${target === 'replace' ? 'ON' : 'OFF'}` }
    }
    if (word === 'now') {
      try {
        const r = await $.session.compact({ instructions: 'keep nothing the log can return' })
        return { text: r.skip ? `not compacted: ${r.skip}` : `compacted; ${lastReport}` }
      } catch (err) {
        return { text: `could not compact: ${String((err as Error)?.message ?? err)}` }
      }
    }
    return { text: [`mode: ${await getMode($)} (/jevreplace on | off | toggle)`, `last: ${lastReport}`, '/jevreplace now compacts between turns'].join('\n') }
  })

  on('session.compact', async ($, e, next) => {
    // `precompute` installs nothing, and a subagent's own transcript is not ours to rewrite.
    if (e.trigger === 'precompute' || e.agentId !== undefined) return next(e)
    const mode = await getMode($)
    $.ui.log(`jev-context-replace: compact hook, trigger=${e.trigger}, mode=${mode}, messages=${e.messages.length}`, { to: 'debug' })
    if (mode !== 'replace') return next(e)
    try {
      const messages = e.messages as readonly Msg[]
      const from = tailStart(messages)
      $.ui.log(`jev-context-replace: tail starts at ${from} of ${messages.length}`, { to: 'debug' })
      if (messages.length < MIN_MESSAGES || from <= 0) return next(e)
      // The part about to be dropped must be in the log first, or it would be lost for good.
      const ing = await fetchJson($, '/ingest', { method: 'POST', body: '{}' }, 30000)
      if (!ing.ok) throw new Error(`ingest failed: ${ing.error ?? 'unknown'}`)
      const query = queryOf(messages)
      let hits: Hit[] = []
      if (query) {
        try {
          const r = await fetchJson($, '/search', { method: 'POST', body: JSON.stringify({ query, limit: 12 }) }, 5000)
          hits = r.ok ? r.hits : []
        } catch {
          // No excerpts is acceptable: the outline and the log tools still carry the history.
        }
      }
      const out = replacement(messages, from, hits)
      if (!out) return next(e)
      lastReport = `${messages.length} messages / ${sizeOf(messages).toLocaleString()} chars -> ${out.length} / ${sizeOf(out).toLocaleString()} chars, ${hits.length} excerpts`
      $.ui.log(`jev-context-replace: ${lastReport}`)
      await update($, lastAtom, () => lastReport)
      return { messages: out }
    } catch (err) {
      $.ui.log(`jev-context-replace: stepped aside (${String((err as Error)?.message ?? err)})`, { to: 'debug' })
      return next(e)
    }
  }).catch(($, e, next) => next(e)) // a failure here must never block a compaction: fail open

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const mode = await read($, modeAtom)
    const last = await read($, lastAtom)
    const { Box, Button, Text } = $.ui.resolve(e)
    const isOn = mode === 'replace'
    return (
      <Box>
        <Text dimColor>{isOn ? 'Jev context: ON' : 'Jev context: off'}{isOn && last ? ` (last: ${last})` : ''} </Text>
        <Button key="toggle" label={isOn ? 'Turn off' : 'Turn on'} onPress={() => setMode($, isOn ? 'off' : 'replace')} />
      </Box>
    )
  })
}
