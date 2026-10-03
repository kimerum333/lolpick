<script setup lang="ts">
import { computed } from 'vue'
import { isOpen, teams, type Match } from '../domain/game'
import { compact, dateTime, points } from '../format'
import { useGameStore } from '../stores/game'
import TeamMark from './TeamMark.vue'
const props = defineProps<{ match: Match; featured?: boolean }>()
defineEmits<{ select: [id: string] }>()
const game = useGameStore()
const open = computed(() => isOpen(props.match, game.now))
const bet = computed(() => game.state.bets.find(item => item.matchId === props.match.id))
const pools = computed(() => props.match.pools.map((pool, i) => pool + (bet.value?.team === props.match.teams[i] ? bet.value.amount : 0)))
const total = computed(() => pools.value[0]! + pools.value[1]!)
const share = computed(() => Math.round(pools.value[0]! / total.value * 100))
const status = computed(() => props.match.status === 'finished' ? '경기 종료' : props.match.status === 'live' ? 'LIVE' : open.value ? '베팅 오픈' : '베팅 마감')
</script>
<template>
  <article class="match-card" :class="{ featured }">
    <div class="match-meta"><span class="status" :class="{ open, live: match.status === 'live' }"><i></i>{{ status }}</span><span>{{ dateTime(match.startsAt) }}</span></div>
    <div class="match-teams"><div class="match-team"><TeamMark :team="match.teams[0]" /><strong>{{ teams[match.teams[0]].name }}</strong><span>{{ teams[match.teams[0]].region }}</span></div><div class="versus"><strong v-if="match.score">{{ match.score[0] }} : {{ match.score[1] }}</strong><strong v-else>VS</strong><span>{{ match.round.split(' · ')[1] }}</span></div><div class="match-team"><TeamMark :team="match.teams[1]" /><strong>{{ teams[match.teams[1]].name }}</strong><span>{{ teams[match.teams[1]].region }}</span></div></div>
    <div class="pool-labels"><span>{{ share }}% <b>{{ (total / pools[0]!).toFixed(2) }}×</b></span><span><b>{{ (total / pools[1]!).toFixed(2) }}×</b> {{ 100 - share }}%</span></div>
    <div class="pool-bar" aria-hidden="true"><span :style="{ width: `${share}%` }"></span></div>
    <div class="pool-summary"><span>총 {{ compact(total) }} P 참여</span><span>예상 배당</span></div>
    <button class="button full" :class="featured ? 'primary' : 'secondary'" @click="$emit('select', match.id)">{{ bet ? `${bet.team} · ${points(bet.amount)} 베팅 완료` : open ? '승리 팀 예측하기' : '경기 상세 보기' }}<span>↗</span></button>
  </article>
</template>
