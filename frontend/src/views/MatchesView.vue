<script setup lang="ts">
import { computed, ref } from 'vue'
import { events, isOpen, type EventId } from '../domain/game'
import { points } from '../format'
import { useGameStore } from '../stores/game'
import MatchCard from '../components/MatchCard.vue'
import BetDialog from '../components/BetDialog.vue'
import CheerPanel from '../components/CheerPanel.vue'
const game = useGameStore()
const event = ref<EventId>('worlds')
const filter = ref('all')
const selectedMatch = ref<string | null>(null)
const filters = [{ id: 'all', label: '전체 경기' }, { id: 'open', label: '베팅 가능' }, { id: 'live', label: '진행 중' }, { id: 'finished', label: '종료' }]
const matches = computed(() => game.matches.filter(match => match.event === event.value && (filter.value === 'all' || (filter.value === 'open' && isOpen(match, game.now)) || (filter.value === 'live' && match.status === 'live') || (filter.value === 'finished' && match.status === 'finished'))))
const openCount = computed(() => game.matches.filter(match => match.event === event.value && isOpen(match, game.now)).length)
</script>
<template>
  <div class="page-heading"><div><div class="section-kicker">MAKE YOUR CALL</div><h1>당신의 예측이,<br />경기의 또 다른 재미.</h1><p class="muted">승리를 예측하고, 내 팀을 응원하고, 기록을 쌓아보세요.</p></div><div class="event-select"><label for="match-event">이벤트</label><select id="match-event" v-model="event"><option v-for="item in events" :key="item.id" :value="item.id">{{ item.label }}</option></select></div></div>
  <div class="dashboard-layout"><div class="main-column">
    <section class="event-banner"><div class="banner-grid" aria-hidden="true"></div><div class="banner-top"><span class="eyebrow">{{ event === 'worlds' ? 'THE WORLD STAGE' : 'MID-SEASON INVITATIONAL' }}</span><span class="banner-pill">{{ event === 'worlds' ? '진행 중' : '이벤트 종료' }}</span></div><h2>{{ event === 'worlds' ? 'WORLDS' : 'MSI' }}<span>2026</span></h2><div class="banner-bottom"><p>{{ event === 'worlds' ? '최고의 무대, 당신의 선택은?' : '지난 이벤트의 경기 결과를 살펴보세요.' }}</p><span>{{ openCount }}개 경기 베팅 가능 <b>↗</b></span></div></section>
    <div class="section-heading"><h2>경기 일정 <span class="count">{{ matches.length }}</span></h2><span class="muted small-text">샘플 일정 · 한국 시간</span></div>
    <div class="filter-tabs" aria-label="경기 상태"><button v-for="item in filters" :key="item.id" :class="{ active: filter === item.id }" :aria-pressed="filter === item.id" @click="filter = item.id">{{ item.label }}</button></div>
    <div v-if="matches.length" class="match-grid"><MatchCard v-for="(match, index) in matches" :key="match.id" :match="match" :featured="index === 0 && isOpen(match, game.now)" @select="selectedMatch = $event" /></div>
    <div v-else class="empty-state"><span>◎</span><h3>해당하는 경기가 없습니다</h3><p>다른 경기 상태나 이벤트를 선택해 주세요.</p><button class="button secondary" @click="filter = 'all'">전체 경기 보기</button></div>
  </div><aside class="side-column">
    <section class="panel wallet-panel"><div class="section-kicker">MY WALLET</div><div class="wallet-title"><span>보유 포인트</span><span class="neutral-tag">샘플 계정</span></div><strong class="wallet-points">{{ points(game.points) }}</strong><div class="wallet-detail"><span>베팅 중</span><b>{{ points(128500 - game.points) }}</b></div><RouterLink to="/profile" class="text-link">내 베팅 기록 <span>→</span></RouterLink></section>
    <CheerPanel />
    <section class="guide-panel"><span class="guide-icon">↗</span><h3>예측은 가볍게, 응원은 진심으로.</h3><p>포인트는 서비스 안에서만 사용하는 가상 재화입니다. 현금 환전과 사용자 간 양도는 불가능합니다.</p><RouterLink to="/guide" class="text-link">처음이라면 이용 가이드 <span>→</span></RouterLink></section>
  </aside></div>
  <BetDialog v-if="selectedMatch" :key="selectedMatch" :match-id="selectedMatch" @close="selectedMatch = null" />
</template>
