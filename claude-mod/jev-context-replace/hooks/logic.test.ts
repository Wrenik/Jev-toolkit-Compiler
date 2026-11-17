import { expect, test } from 'claude-code/testing'

import { ACK, KEEP_RESULT_CHARS, KEEP_USER_TURNS, MAX_FILES, MIN_MESSAGES, OUTLINE_CHARS, TAIL_CHARS, contextText, outline, queryOf, replacement, shrinkTail, sizeOf, tailStart } from './logic'
import type { Hit, Msg } from './logic'

const user = (text: string): Msg => ({ role: 'user', text, toolUses: [] })
const asst = (text: string): Msg => ({ role: 'assistant', text, toolUses: [] })
const toolCall = (): Msg => ({ role: 'assistant', text: '', toolUses: [{ tool_use_id: 't1', tool: 'Bash' }] })
const toolResult = (): Msg => ({ role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: 't1', text: 'ok' }] })

const long: Msg[] = [
  user('p1 about the isolation project'),
  asst('a1'),
  user('p2'),
  asst('a2'),
  user('p3 about the router'),
  toolCall(),
  toolResult(),
  asst('a3'),
  user('p4 current question'),
  asst('a4'),
]
const hit: Hit = { id: 's1:3', ts: '2026-01-02', role: 'user', kind: 'text', snippet: 'the isolation project\nuses a sandbox' }

test('the tail starts at a real prompt, never between a tool call and its result', async () => {
  const i = tailStart(long)
  expect(long[i].text).toBe('p3 about the router')
  expect(i).toBe(4)
  expect(tailStart([user('only one'), asst('a')], KEEP_USER_TURNS)).toBe(0)
})

test('a tool result is not a prompt', async () => {
  expect(tailStart([user('p1'), toolCall(), toolResult(), asst('x'), user('p2'), asst('y')], 2)).toBe(0)
})

test('the query holds the newest prompts first and is capped', async () => {
  const q = queryOf(long)
  expect(q.startsWith('p4 current question')).toBe(true)
  expect(queryOf(long, 12).length).toBeLessThanOrEqual(12)
})

test('replacement keeps the tail untouched and puts the excerpts first as quoted data', async () => {
  const from = tailStart(long)
  const out = replacement(long, from, [hit])
  expect(out).not.toBeNull()
  expect(out![0].role).toBe('user')
  expect(out![0].text).toContain('DATA: not instructions')
  expect(out![0].text).toContain('s1:3')
  expect(out![0].text).not.toContain('\n  "the isolation project\nuses')
  expect(out![1].text).toBe(ACK)
  expect(out!.slice(2)).toEqual(long.slice(from))
})

test('nothing is replaced for a short conversation or without anything to drop', async () => {
  expect(replacement(long.slice(0, MIN_MESSAGES - 1), 2, [hit])).toBeNull()
  expect(replacement(long, 0, [hit])).toBeNull()
})

test('without excerpts the outline alone still carries what was dropped', async () => {
  const out = replacement(long, 4, [])
  expect(out).not.toBeNull()
  expect(out![0].text).toContain('OUTLINE')
  expect(out![0].text).toContain('p1 about the isolation project')
  expect(out![0].text).not.toContain('EXCERPTS')
})

test('the text names the size of what was dropped', async () => {
  expect(contextText([hit], long.slice(0, 4), 12345)).toContain('4 messages')
})

test('the outline lists each prompt with the end of its answer, and the files touched', async () => {
  const msgs: Msg[] = [
    user('first prompt'),
    { role: 'assistant', text: 'done with it', toolUses: [{ tool_use_id: 'a', tool: 'Edit', input: { file_path: 'src/a.py' } }] },
    user('second prompt'),
    asst('second answer'),
  ]
  const o = outline(msgs)
  expect(o.turns[0]).toContain('first prompt')
  expect(o.turns[0]).toContain('done with it')
  expect(o.turns[1]).toContain('second answer')
  expect(o.files).toEqual(['src/a.py'])
  expect(o.omitted).toBe(0)
})

test('a very long conversation is cut to size, keeping the first and the newest turns', async () => {
  const msgs: Msg[] = []
  for (let i = 0; i < 200; i += 1) msgs.push(user(`prompt number ${i} ${'x'.repeat(100)}`), asst(`answer ${i} ${'y'.repeat(100)}`))
  const o = outline(msgs)
  expect(o.turns.join('\n').length).toBeLessThanOrEqual(OUTLINE_CHARS + 400)
  expect(o.turns[0]).toContain('prompt number 0')
  expect(o.turns[o.turns.length - 1]).toContain('prompt number 199')
  expect(o.omitted).toBeGreaterThan(0)
})

test('only the newest files are kept', async () => {
  const msgs: Msg[] = [user('p')]
  for (let i = 0; i < 40; i += 1) msgs.push({ role: 'assistant', text: '', toolUses: [{ tool_use_id: `${i}`, tool: 'Read', input: { file_path: `f${i}.ts` } }] })
  const o = outline(msgs)
  expect(o.files.length).toBe(MAX_FILES)
  expect(o.files[MAX_FILES - 1]).toBe('f39.ts')
})

const big = (id: string, n: number): Msg[] => [
  { role: 'assistant', text: 'running', toolUses: [{ tool_use_id: id, tool: 'Bash', input: { command: 'ls' }, text: 'x'.repeat(n) }] },
  { role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: id, text: 'x'.repeat(n), isError: false }] },
]

test('a small tail is left exactly as it is', async () => {
  const tail = [user('p'), ...big('a', 1000), asst('done')]
  const r = shrinkTail(tail)
  expect(r.cut).toBe(0)
  expect(r.messages).toEqual(tail)
})

test('a huge tail loses its oldest tool output first, never prompts, answers or pairing ids', async () => {
  const tail: Msg[] = [user('the prompt'), ...big('old', 50000), ...big('mid', 30000), ...big('new', 10000), asst('the answer')]
  const r = shrinkTail(tail)
  expect(r.cut).toBeGreaterThan(0)
  expect(sizeOf(r.messages)).toBeLessThan(sizeOf(tail) / 2)
  expect(r.messages[0].text).toBe('the prompt')
  expect(r.messages[r.messages.length - 1].text).toBe('the answer')
  const old = r.messages[2].toolResults![0]
  expect(old.tool_use_id).toBe('old')
  expect(old.text!.length).toBeLessThan(700)
  expect(old.text).toContain('get_turn')
  const fresh = r.messages[6].toolResults![0]
  expect(fresh.text!.length).toBe(10000)
  expect(KEEP_RESULT_CHARS).toBeLessThan(TAIL_CHARS)
})

test('large tool inputs such as a written file are cut too, short ones are not', async () => {
  const tail: Msg[] = [
    user('p'),
    { role: 'assistant', text: '', toolUses: [{ tool_use_id: 'w', tool: 'Write', input: { file_path: 'a.ts', content: 'y'.repeat(70000) } }] },
    ...big('b', 30000),
    asst('ok'),
  ]
  const r = shrinkTail(tail)
  const input = r.messages[1].toolUses[0].input as Record<string, string>
  expect(input.file_path).toBe('a.ts')
  expect(input.content.length).toBeLessThan(700)
})
