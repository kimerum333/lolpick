<script setup lang="ts">
import { computed, ref } from 'vue'
import { useGameStore } from '../stores/game'
import { number, points } from '../format'
import TeamMark from '../components/TeamMark.vue'
import type { TeamId } from '../domain/game'
const game = useGameStore()
const event = ref('worlds')
const samples: { name: string; team?: TeamId; worlds: number; msi: number; hits: number; count: number; msiHits: number; msiCount: number }[] = [
  { name: '역배의신', team: 'GEN', worlds: 892400, msi: 246000, hits: 15, count: 24, msiHits: 7, msiCount: 12 },
  { name: '페이커의오른팔', team: 'T1', worlds: 764200, msi: 398000, hits: 14, count: 21, msiHits: 10, msiCount: 14 },
  { name: '젠지우승가자', team: 'GEN', worlds: 701500, msi: 580000, hits: 16, count: 26, msiHits: 11, msiCount: 15 },
  { name: '바론앞5대5', worlds: 645000, msi: 420000, hits: 13, count: 18, msiHits: 8, msiCount: 11 },
  { name: '티원화이팅', team: 'T1', worlds: 590300, msi: 321000, hits: 14, count: 20, msiHits: 9, msiCount: 14 },
  { name: '한타는끝까지', team: 'HLE', worlds: 425000, msi: 180000, hits: 12, count: 22, msiHits: 6, msiCount: 10 },
]
const rows = computed(() => samples.map(row => ({
  ...row,
  profit: event.value === 'all' ? row.worlds + row.msi : event.value === 'worlds' ? row.worlds : row.msi,
  hits: event.value === 'all' ? row.hits + row.msiHits : event.value === 'worlds' ? row.hits : row.msiHits,
  count: event.value === 'all' ? row.count + row.msiCount : event.value === 'worlds' ? row.count : row.msiCount,
  team: event.value === 'msi' ? undefined : row.team,
})).sort((a, b) => b.profit - a.profit))
</script>
<template>
  <div class="page-heading"><div><div class="section-kicker">THE LEADERBOARD</div><h1>예측의 실력은<br />기록으로 남습니다.</h1><p class="muted">포인트 보유량이 아닌, 베팅 손익으로 경쟁하세요.</p></div></div>
  <div class="ranking-toolbar"><div class="filter-tabs"><button v-for="item in [{ id: 'all', name: '통합' }, { id: 'worlds', name: 'Worlds' }, { id: 'msi', name: 'MSI' }]" :key="item.id" :class="{ active: event === item.id }" :aria-pressed="event === item.id" @click="event = item.id">{{ item.name }}</button></div><span class="neutral-tag">2026 · 샘플 랭킹</span></div>
  <div class="podium"><article v-for="(row, index) in rows.slice(0, 3)" :key="row.name" class="panel podium-card" :class="{ first: index === 0 }"><span class="podium-place">0{{ index + 1 }}<span>{{ index === 0 ? 'TOP PREDICTOR' : 'PREDICTOR' }}</span></span><div class="podium-user"><TeamMark v-if="row.team" :team="row.team" small /><span v-else class="avatar">바</span><h2>{{ row.name }}</h2></div><strong class="profit">+{{ points(row.profit) }}</strong><p class="muted small-text">적중률 {{ (row.hits / row.count * 100).toFixed(1) }}% · {{ row.count }}경기 참여</p></article></div>
  <section class="panel ranking-table"><div class="section-heading"><h2>전체 랭킹</h2><span class="muted small-text">정산된 베팅 손익 기준</span></div><div class="table-scroll"><table><thead><tr><th>순위</th><th>유저</th><th>베팅 손익</th><th>적중률</th><th>참여 경기</th></tr></thead><tbody><tr v-for="(row, index) in rows" :key="row.name"><td class="rank-number">{{ index + 1 }}</td><td><span class="table-user"><TeamMark v-if="row.team" :team="row.team" small /><span v-else class="avatar">바</span>{{ row.name }}</span></td><td class="profit">+{{ number(row.profit) }} P</td><td>{{ (row.hits / row.count * 100).toFixed(1) }}%</td><td>{{ row.count }}</td></tr><tr class="my-rank"><td>—</td><td><span class="table-user"><TeamMark v-if="event !== 'msi' && game.contract && game.seal" :team="game.contract.team" small /><span>오늘의예측가 <b class="me-label">나</b></span></span></td><td>0 P</td><td>—</td><td>0</td></tr></tbody></table></div><p class="muted small-text table-note">내 순위는 첫 정산 이후 표시됩니다. 현재 베팅은 결과 확정 전으로 랭킹에 반영되지 않습니다.</p></section>
</template>
