<script setup lang="ts">
import { computed, ref } from 'vue'
import { useGameStore } from '../stores/game'
import { INITIAL_POINTS, teams } from '../domain/game'
import { dateTime, points } from '../format'
import TeamMark from '../components/TeamMark.vue'
import CheerPanel from '../components/CheerPanel.vue'
const game = useGameStore()
const resetConfirm = ref(false)
const scope = ref('all')
const bets = computed(() => [...game.state.bets].reverse().filter(bet => scope.value === 'all' || game.matches.find(match => match.id === bet.matchId)?.event === scope.value))
const spent = computed(() => game.state.bets.reduce((sum, bet) => sum + bet.amount, 0))
function title(id: string) { const match = game.matches.find(item => item.id === id); return match ? `${match.teams[0]} vs ${match.teams[1]}` : '경기' }
</script>
<template>
  <div class="page-heading"><div><div class="section-kicker">MY RECORD</div><h1>오늘의예측가<span class="profile-dot">.</span></h1><p class="muted">매 경기의 선택이 나만의 기록이 됩니다.</p></div><span class="neutral-tag">샘플 계정</span></div>
  <div class="profile-stats"><section class="panel stat"><span>보유 포인트</span><strong>{{ points(game.points) }}</strong><p>초기 지급 {{ points(INITIAL_POINTS) }}</p></section><section class="panel stat"><span>베팅 중인 포인트</span><strong>{{ points(spent) }}</strong><p>{{ game.state.bets.length }}경기 · 정산 대기</p></section><section class="panel stat"><span>확정 베팅 손익</span><strong>0 P</strong><p>첫 결과를 기다리고 있어요</p></section></div>
  <div class="dashboard-layout"><div class="main-column"><section class="panel history-panel"><div class="section-heading"><h2>내 베팅 기록</h2><select v-model="scope" aria-label="베팅 기록 이벤트"><option value="all">통합</option><option value="worlds">2026 Worlds</option><option value="msi">2026 MSI</option></select></div><div v-if="!bets.length" class="empty-state"><span>↗</span><h3>첫 번째 예측을 기다립니다</h3><p>경기를 선택하고 승리할 팀에 포인트를 걸어보세요.</p><RouterLink to="/" class="button primary">경기 둘러보기</RouterLink></div><ul v-else class="bet-history"><li v-for="bet in bets" :key="bet.matchId"><TeamMark :team="bet.team" small /><div class="history-match"><strong>{{ title(bet.matchId) }}</strong><span>{{ teams[bet.team].name }} 승리 예측 · {{ dateTime(bet.createdAt) }}</span></div><div class="history-amount"><strong>{{ points(bet.amount) }}</strong><span class="neutral-tag">정산 대기</span></div></li></ul></section><section class="panel record-panel"><div class="section-heading"><h2>응원 기록</h2><span class="muted small-text">2026 Worlds</span></div><template v-if="game.contract"><div class="contract-team"><TeamMark :team="game.contract.team" /><div><strong>{{ teams[game.contract.team].name }}</strong><span>{{ game.seal ? '✦ 응원 인장 유지 중' : '응원 인장 박탈' }}</span></div></div><div class="record-numbers"><div><span>응원 베팅</span><strong>{{ game.cheeringCount }}회</strong></div><div><span>배신 베팅</span><strong>{{ game.betrayalCount }}회</strong></div><div><span>배신률</span><strong>{{ game.cheeringCount + game.betrayalCount ? (game.betrayalCount / (game.cheeringCount + game.betrayalCount) * 100).toFixed(1) : '0' }}%</strong></div></div></template><p v-else class="muted">아직 응원 계약이 없습니다. 이벤트의 첫 베팅 전에 팀을 선택해 주세요.</p></section></div><aside class="side-column"><CheerPanel /><section class="panel reset-panel"><h3>샘플 계정 초기화</h3><p class="muted small-text">베팅과 응원 계약을 지우고 처음부터 체험할 수 있습니다.</p><template v-if="resetConfirm"><p class="warning">저장된 샘플 기록이 삭제됩니다.</p><div class="button-row"><button class="button secondary" @click="resetConfirm = false">취소</button><button class="button primary" @click="game.reset(); resetConfirm = false">초기화 확정</button></div></template><button v-else class="button secondary full" @click="resetConfirm = true">체험 다시 시작</button></section></aside></div>
</template>
