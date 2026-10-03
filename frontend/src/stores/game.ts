import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { balance, createMatches, hasSeal, isBetrayal, placeBet, restoreState, signContract, type EventId, type TeamId } from '../domain/game'

const KEY = 'lolpick.demo.v1'
export const useGameStore = defineStore('game', () => {
  let saved: string | null = null
  try {
    saved = localStorage.getItem(KEY)
    if (saved === null) {
      // Migrate records saved before the project was renamed.
      const legacyKey = 'lolto.demo.v1'
      saved = localStorage.getItem(legacyKey)
      if (saved !== null) {
        localStorage.setItem(KEY, saved)
        localStorage.removeItem(legacyKey)
      }
    }
  } catch { /* Storage can be unavailable in private browsers. */ }
  const state = ref(restoreState(saved, Date.now()))
  const now = ref(Date.now())
  const storageWarning = ref('')
  const matches = computed(() => createMatches(state.value.anchor))
  const points = computed(() => balance(state.value))
  const contract = computed(() => state.value.contracts.find(item => item.event === 'worlds'))
  const seal = computed(() => contract.value ? hasSeal(state.value, contract.value, matches.value) : false)
  const cheeringCount = computed(() => state.value.bets.filter(bet => {
    const match = matches.value.find(item => item.id === bet.matchId)
    return contract.value && match?.event === contract.value.event && bet.team === contract.value.team
  }).length)
  const betrayalCount = computed(() => contract.value ? state.value.bets.filter(bet => isBetrayal(bet, contract.value!, matches.value)).length : 0)
  watch(state, value => {
    try { localStorage.setItem(KEY, JSON.stringify({ version: 1, ...value })) }
    catch { storageWarning.value = '브라우저 저장 공간을 사용할 수 없어 새로고침하면 기록이 초기화됩니다.' }
  }, { deep: true, flush: 'sync' })
  function bet(matchId: string, team: TeamId, amount: number) {
    const match = matches.value.find(item => item.id === matchId)
    if (!match) throw new Error('경기를 찾을 수 없습니다.')
    state.value = placeBet(state.value, match, team, amount, Date.now())
  }
  function cheer(event: EventId, team: TeamId) { state.value = signContract(state.value, event, team, matches.value) }
  function reset() { state.value = { anchor: Date.now(), bets: [], contracts: [] }; now.value = Date.now() }
  return { state, now, matches, points, contract, seal, cheeringCount, betrayalCount, storageWarning, bet, cheer, reset }
})
