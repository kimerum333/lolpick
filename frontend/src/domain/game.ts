export type TeamId = 'GEN' | 'T1' | 'HLE' | 'BLG' | 'G2' | 'TES'
export type EventId = 'worlds' | 'msi'
export interface Team { id: TeamId; name: string; region: string; color: string }
export interface Match {
  id: string; event: EventId; round: string; teams: [TeamId, TeamId]
  pools: [number, number]; startsAt: number; status: 'scheduled' | 'live' | 'finished'
  score?: [number, number]; winner?: TeamId
}
export interface Bet { matchId: string; team: TeamId; amount: number; createdAt: number }
export interface Contract { event: EventId; team: TeamId }
export interface GameState { anchor: number; bets: Bet[]; contracts: Contract[] }
export const INITIAL_POINTS = 128_500
export const teams: Record<TeamId, Team> = {
  GEN: { id: 'GEN', name: 'Gen.G', region: 'LCK', color: '#a88a47' },
  T1: { id: 'T1', name: 'T1', region: 'LCK', color: '#d94347' },
  HLE: { id: 'HLE', name: 'Hanwha Life', region: 'LCK', color: '#ee7d36' },
  BLG: { id: 'BLG', name: 'Bilibili Gaming', region: 'LPL', color: '#35989c' },
  G2: { id: 'G2', name: 'G2 Esports', region: 'LEC', color: '#5b6374' },
  TES: { id: 'TES', name: 'Top Esports', region: 'LPL', color: '#bb4932' },
}
export const events = [
  { id: 'worlds' as const, name: 'Worlds', year: 2026, label: '2026 Worlds', active: true, teams: ['GEN', 'T1', 'HLE', 'BLG', 'G2', 'TES'] as TeamId[] },
  { id: 'msi' as const, name: 'MSI', year: 2026, label: '2026 MSI', active: false, teams: ['GEN', 'T1', 'BLG', 'G2'] as TeamId[] },
]
export function createMatches(anchor: number): Match[] {
  const hour = 3_600_000
  return [
    { id: 'w1', event: 'worlds', round: '스위스 스테이지 · BO3', teams: ['GEN', 'T1'], pools: [6_480_000, 3_920_000], startsAt: anchor + hour * 2, status: 'scheduled' },
    { id: 'w2', event: 'worlds', round: '스위스 스테이지 · BO3', teams: ['HLE', 'BLG'], pools: [2_340_000, 1_860_000], startsAt: anchor + hour * 5, status: 'scheduled' },
    { id: 'w3', event: 'worlds', round: '스위스 스테이지 · BO3', teams: ['G2', 'TES'], pools: [1_420_000, 2_580_000], startsAt: anchor + hour * 26, status: 'scheduled' },
    { id: 'w4', event: 'worlds', round: '스위스 스테이지 · BO3', teams: ['T1', 'G2'], pools: [3_800_000, 1_200_000], startsAt: anchor - hour, status: 'live', score: [1, 0] },
    { id: 'w5', event: 'worlds', round: '스위스 스테이지 · BO3', teams: ['GEN', 'BLG'], pools: [4_600_000, 2_400_000], startsAt: anchor - hour * 24, status: 'finished', score: [2, 1], winner: 'GEN' },
    { id: 'm1', event: 'msi', round: '결승 · BO5', teams: ['GEN', 'T1'], pools: [8_000_000, 4_000_000], startsAt: anchor - hour * 24 * 80, status: 'finished', score: [3, 2], winner: 'GEN' },
  ]
}
export function balance(state: GameState): number {
  return INITIAL_POINTS - state.bets.reduce((sum, bet) => sum + bet.amount, 0)
}
export function isOpen(match: Match, now: number): boolean {
  return match.status === 'scheduled' && match.startsAt > now
}
export function placeBet(state: GameState, match: Match, team: TeamId, amount: number, now: number): GameState {
  if (!isOpen(match, now)) throw new Error('베팅이 마감된 경기입니다.')
  if (!match.teams.includes(team)) throw new Error('이 경기에 출전하는 팀을 선택해 주세요.')
  if (state.bets.some(bet => bet.matchId === match.id)) throw new Error('이미 베팅한 경기입니다. 추가 베팅 규칙은 준비 중입니다.')
  if (!Number.isSafeInteger(amount) || amount < 1) throw new Error('1P 이상의 정수 금액을 입력해 주세요.')
  if (amount > balance(state)) throw new Error('보유 포인트가 부족합니다.')
  return { ...state, bets: [...state.bets, { matchId: match.id, team, amount, createdAt: now }] }
}
export function signContract(state: GameState, eventId: EventId, team: TeamId, matches: Match[]): GameState {
  const event = events.find(item => item.id === eventId)
  if (!event?.active) throw new Error('진행 중인 이벤트에서 응원 계약을 맺을 수 있습니다.')
  if (!event.teams.includes(team)) throw new Error('이 이벤트에 참가하는 팀을 선택해 주세요.')
  if (state.contracts.some(item => item.event === eventId)) throw new Error('이벤트 진행 중에는 응원 팀을 변경할 수 없습니다.')
  if (state.bets.some(bet => matches.find(match => match.id === bet.matchId)?.event === eventId)) throw new Error('이벤트의 첫 베팅 이후에는 응원 계약을 체결할 수 없습니다.')
  return { ...state, contracts: [...state.contracts, { event: eventId, team }] }
}
export function isBetrayal(bet: Bet, contract: Contract, matches: Match[]): boolean {
  const match = matches.find(item => item.id === bet.matchId)
  return !!match && match.event === contract.event && match.teams.includes(contract.team) && bet.team !== contract.team
}
export function hasSeal(state: GameState, contract: Contract, matches: Match[]): boolean {
  return !state.bets.some(bet => isBetrayal(bet, contract, matches))
}
export function restoreState(raw: string | null, now: number): GameState {
  const fallback: GameState = { anchor: now, bets: [], contracts: [] }
  if (!raw) return fallback
  try {
    const data = JSON.parse(raw)
    if (data.version !== 1 || !Number.isSafeInteger(data.anchor) || data.anchor < 1 || data.anchor > now || !Array.isArray(data.bets) || !Array.isArray(data.contracts)) return fallback
    const matches = createMatches(data.anchor)
    let state: GameState = { anchor: data.anchor, bets: [], contracts: [] }
    for (const contract of data.contracts) state = signContract(state, contract.event, contract.team, matches)
    for (const bet of data.bets) {
      const match = matches.find(item => item.id === bet.matchId)
      if (!match || !Number.isSafeInteger(bet.createdAt) || bet.createdAt < data.anchor || bet.createdAt > now) return fallback
      state = placeBet(state, match, bet.team, bet.amount, bet.createdAt)
    }
    return state
  } catch { return fallback }
}
