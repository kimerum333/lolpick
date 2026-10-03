<script setup lang="ts">
import { onMounted, onBeforeUnmount, watch, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useGameStore } from './stores/game'
import { points } from './format'
const game = useGameStore()
const route = useRoute()
const main = ref<HTMLElement>()
let timer: ReturnType<typeof setInterval>
onMounted(() => { timer = setInterval(() => { game.now = Date.now() }, 1000) })
onBeforeUnmount(() => clearInterval(timer))
watch(() => route.path, () => { main.value?.focus() }, { flush: 'post' })
const nav = [{ path: '/', icon: '◫', label: '경기', sub: 'MATCHES' }, { path: '/ranking', icon: '↗', label: '랭킹', sub: 'RANKING' }, { path: '/profile', icon: '◎', label: '내 프로필', sub: 'PROFILE' }]
</script>
<template>
  <a href="#main-content" class="skip-link" @click.prevent="main?.focus()">본문으로 이동</a>
  <aside class="sidebar"><RouterLink to="/" class="brand" aria-label="LOLPICK 홈">lolpick<span>↗</span></RouterLink><span class="brand-caption">MAKE YOUR CALL.</span><nav aria-label="주 메뉴"><RouterLink v-for="item in nav" :key="item.path" :to="item.path" class="nav-item" :class="{ active: route.path === item.path }"><span class="nav-icon">{{ item.icon }}</span><span>{{ item.label }}<small>{{ item.sub }}</small></span><b>›</b></RouterLink></nav><div class="sidebar-bottom"><span class="sidebar-mini">PLAY WITH POINTS.<br />CHEER WITH HEART.</span><RouterLink to="/guide" class="guide-link" :class="{ active: route.path === '/guide' }">이용 가이드 <span>↗</span></RouterLink><span class="copyright">© 2026 LOLPICK</span></div></aside>
  <div class="workspace"><header class="topbar"><div class="topbar-context"><span class="live-dot"></span>LoL ESPORTS <span class="topbar-divider">/</span><b>{{ route.meta.title }}</b></div><div class="account"><RouterLink to="/profile" class="header-points">{{ points(game.points) }}</RouterLink><RouterLink to="/profile" class="account-link"><span class="avatar">예</span><span>오늘의예측가<small>샘플 계정</small></span></RouterLink></div></header><div class="demo-strip"><span class="demo-tag">PREVIEW</span> 샘플 경기와 가상 포인트로 체험하는 프론트엔드입니다.<RouterLink to="/guide">안내 보기 →</RouterLink></div><p v-if="game.storageWarning" role="alert" class="storage-warning">{{ game.storageWarning }}</p><main id="main-content" ref="main" tabindex="-1"><RouterView /></main><footer class="site-footer"><span>예측의 즐거움, 응원의 기록.</span><span>가상 포인트 전용 · 현금 환전 불가</span></footer></div>
</template>
