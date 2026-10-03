import test from 'node:test'
import assert from 'node:assert/strict'
import { INITIAL_POINTS, balance, createMatches, hasSeal, placeBet, restoreState, signContract } from '../src/domain/game.ts'

const now = 1_790_000_000_000
const initial = () => ({ anchor: now, bets: [], contracts: [] })
const matches = createMatches(now)
const match = matches[0]

test('a bet deducts points once and rejects a second bet on either team', () => {
  const state = placeBet(initial(), match, 'T1', 5000, now)
  assert.equal(balance(state), INITIAL_POINTS - 5000)
  assert.equal(initial().bets.length, 0)
  for (const team of match.teams) assert.throws(() => placeBet(state, match, team, 1000, now), /이미 베팅/)
})
test('invalid stakes and teams cannot change the wallet', () => {
  for (const amount of [0, -1, 1.5, NaN, Infinity, INITIAL_POINTS + 1]) assert.throws(() => placeBet(initial(), match, 'T1', amount, now))
  assert.throws(() => placeBet(initial(), match, 'G2', 1000, now), /출전/)
  assert.equal(balance(initial()), INITIAL_POINTS)
})
test('betting closes at start time and for live and finished matches', () => {
  assert.throws(() => placeBet(initial(), match, 'T1', 1000, match.startsAt), /마감/)
  for (const closed of matches.filter(item => item.status !== 'scheduled')) assert.throws(() => placeBet(initial(), closed, closed.teams[0], 1000, now), /마감/)
})
test('an active contract locks the team even before any bet', () => {
  const state = signContract(initial(), 'worlds', 'T1', matches)
  assert.ok(hasSeal(state, state.contracts[0], matches))
  assert.throws(() => signContract(state, 'worlds', 'GEN', matches), /변경/)
  assert.throws(() => signContract(initial(), 'msi', 'T1', matches), /진행 중/)
  assert.throws(() => signContract(placeBet(initial(), match, 'GEN', 1000, now), 'worlds', 'T1', matches), /첫 베팅/)
})
test('betrayal removes the seal while preserving the contract', () => {
  let state = signContract(initial(), 'worlds', 'T1', matches)
  state = placeBet(state, matches[1], 'BLG', 1000, now)
  assert.ok(hasSeal(state, state.contracts[0], matches), 'unrelated teams do not affect the seal')
  state = placeBet(state, match, 'GEN', 1000, now)
  assert.equal(hasSeal(state, state.contracts[0], matches), false)
  assert.equal(state.contracts[0].team, 'T1')
  const next = { ...match, id: 'next', teams: ['T1', 'TES'] }
  state = placeBet(state, next, 'T1', 1000, now)
  assert.equal(hasSeal(state, state.contracts[0], [...matches, next]), false, 'a later cheering bet cannot restore the seal')
  assert.throws(() => signContract(state, 'worlds', 'GEN', matches), /변경/)
})
test('restoring saved bets after the match starts preserves points and seal loss', () => {
  let state = signContract(initial(), 'worlds', 'T1', matches)
  state = placeBet(state, match, 'GEN', 5000, now)
  const restored = restoreState(JSON.stringify({ version: 1, ...state }), match.startsAt + 1000)
  assert.deepEqual(restored, state)
  assert.equal(balance(restored), INITIAL_POINTS - 5000)
  assert.equal(hasSeal(restored, restored.contracts[0], matches), false)
})
test('corrupt and unsupported saved data falls back to a fresh state', () => {
  for (const raw of ['{', '{}', 'null', JSON.stringify({ version: 2, ...initial() }), JSON.stringify({ version: 1, ...initial(), bets: [{ matchId: 'w1', team: 'T1', amount: -500, createdAt: now }] })]) assert.deepEqual(restoreState(raw, now), initial())
})
