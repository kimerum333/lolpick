<script setup lang="ts">
import { computed, ref } from 'vue'
import { events, teams, type TeamId } from '../domain/game'
import { useGameStore } from '../stores/game'
import TeamMark from './TeamMark.vue'
const game = useGameStore()
const selected = ref<TeamId>('T1')
const confirming = ref(false)
const error = ref('')
const canSign = computed(() => !game.contract && !game.state.bets.some(bet => game.matches.find(match => match.id === bet.matchId)?.event === 'worlds'))
function confirm() {
  try { game.cheer('worlds', selected.value); confirming.value = false }
  catch (e) { error.value = e instanceof Error ? e.message : '계약을 체결하지 못했습니다.' }
}
</script>
<template>
  <section class="panel cheer-panel">
    <div class="section-kicker">MY TEAM</div>
    <h2>이번 이벤트, 누구의 편인가요?</h2>
    <template v-if="game.contract">
      <div class="contract-team"><TeamMark :team="game.contract.team" /><div><strong>{{ teams[game.contract.team].name }}</strong><span>2026 Worlds 응원 계약</span></div></div>
      <p class="seal-label" :class="{ lost: !game.seal }">{{ game.seal ? '✦ 응원 인장 유지 중' : '응원 인장 박탈' }}</p>
      <p class="muted small-text">이벤트 진행 중에는 팀을 변경할 수 없습니다. 상대 팀에 베팅하면 인장을 잃습니다.</p>
    </template>
    <template v-else-if="canSign">
      <p class="muted small-text">첫 베팅 전에 응원 팀을 선택하고 인장을 받아보세요.</p>
      <div class="cheer-options"><button v-for="team in events[0]!.teams" :key="team" :aria-pressed="selected === team" :class="{ selected: selected === team }" @click="selected = team; confirming = false"><TeamMark :team="team" small /></button></div>
      <template v-if="confirming"><p class="small-text">{{ teams[selected].name }}와 계약할까요? 이벤트 진행 중에는 변경할 수 없습니다.</p><div class="button-row"><button class="button secondary" @click="confirming = false">취소</button><button class="button primary" @click="confirm">계약 확정</button></div></template>
      <button v-else class="button secondary full" @click="confirming = true">{{ teams[selected].name }} 응원하기 <span>→</span></button>
    </template>
    <template v-else><p class="muted small-text">이미 이번 이벤트의 첫 베팅에 참여했습니다. 다음 이벤트에서 응원 계약을 맺을 수 있습니다.</p><span class="neutral-tag">응원 계약 없음</span></template>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>
