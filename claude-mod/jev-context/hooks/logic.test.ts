import { expect, test } from 'claude-code/testing'

import { INGEST_MIN_GAP_MS, MAX_PROMPTS, MIN_GAP_MS, describe, pushPrompt, shouldAsk, shouldIngest, toastFor } from './logic'
import type { AskInput } from './logic'

const base: AskInput = { mode: 'advise', contextTokens: 300_000, now: 1_000_000, lastAskedAt: null, isSubagent: false }

test('asks for a big context', async () => {
  expect(shouldAsk(base)).toBe(true)
})

test('never asks when off, in a subagent, or for a small or unknown context', async () => {
  expect(shouldAsk({ ...base, mode: 'off' })).toBe(false)
  expect(shouldAsk({ ...base, isSubagent: true })).toBe(false)
  expect(shouldAsk({ ...base, contextTokens: 50_000 })).toBe(false)
  expect(shouldAsk({ ...base, contextTokens: null })).toBe(false)
})

test('is throttled', async () => {
  expect(shouldAsk({ ...base, lastAskedAt: base.now - 1000 })).toBe(false)
  expect(shouldAsk({ ...base, lastAskedAt: base.now - MIN_GAP_MS - 1 })).toBe(true)
})

test('keeps only the last prompts', async () => {
  let p: string[] = []
  for (let i = 0; i < 10; i += 1) p = pushPrompt(p, `p${i}`)
  expect(p.length).toBe(MAX_PROMPTS)
  expect(p[p.length - 1]).toBe('p9')
})

test('keep and failures stay silent; compact and clear speak', async () => {
  expect(toastFor({ ok: true, action: 'keep' })).toBeNull()
  expect(toastFor({ ok: false, error: 'timeout' })).toBeNull()
  expect(toastFor({ ok: true, action: 'compact_now', break_even_turns: 9.65, reason: 'r' })).toContain('/compact')
  expect(toastFor({ ok: true, action: 'clear_with_seed' })).toContain('/clear')
})

test('describe covers every case', async () => {
  expect(describe(null)).toBe('no advice yet')
  expect(describe({ ok: false, error: 'x' })).toContain('x')
  expect(describe({ ok: true, action: 'keep', reason: 'r', judge_used: false })).toContain('unavailable')
})

test('auto-ingest runs the first time, then at most once per gap, and never when switched off', async () => {
  const now = 10_000_000
  expect(shouldIngest({ enabled: true, now, lastAt: null })).toBe(true)
  expect(shouldIngest({ enabled: true, now, lastAt: now - 1000 })).toBe(false)
  expect(shouldIngest({ enabled: true, now, lastAt: now - INGEST_MIN_GAP_MS })).toBe(true)
  expect(shouldIngest({ enabled: false, now, lastAt: null })).toBe(false)
})
