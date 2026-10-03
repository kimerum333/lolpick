<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { isOpen, teams, type TeamId } from '../domain/game'
import { dateTime, points } from '../format'
import { useGameStore } from '../stores/game'
import TeamMark from './TeamMark.vue'
const props = defineProps<{ matchId: string }>()
const emit = defineEmits<{ close: [] }>()
const game = useGameStore()
const match = computed(() => game.matches.find(item => item.id === props.matchId)!)
const selected = ref<TeamId>(match.value.teams[0])
const amount = ref<number | string>(5_000)
const dialog = ref<HTMLDialogElement>()
const error = ref('')
const success = ref(false)
const existing = computed(() => game.state.bets.find(item => item.matchId === props.matchId))
const open = computed(() => isOpen(match.value, game.now))
const valid = computed(() => Number.isSafeInteger(Number(amount.value)) && Number(amount.value) > 0 && Number(amount.value) <= game.points)
const betrayal = computed(() => game.contract && game.contract.event === match.value.event && match.value.teams.includes(game.contract.team) && selected.value !== game.contract.team)
const estimate = computed(() => {
  const index = match.value.teams.indexOf(selected.value)
  const stake = valid.value ? Number(amount.value) : 0
  return (match.value.pools[0] + match.value.pools[1] + stake) / (match.value.pools[index]! + stake)
})
watch([selected, amount], () => { error.value = '' })
const previousFocus = document.activeElement as HTMLElement | null
onMounted(() => { dialog.value?.showModal() })
onBeforeUnmount(() => { dialog.value?.close(); previousFocus?.focus() })
function submit() {
  try { game.bet(props.matchId, selected.value, Number(amount.value)); success.value = true }
  catch (e) { error.value = e instanceof Error ? e.message : '베팅하지 못했습니다.' }
}
</script>
<template>
  <dialog ref="dialog" class="bet-dialog" aria-labelledby="bet-title" @cancel.prevent="emit('close')" @click="event => { if (event.target === dialog) emit('close') }">
    <div class="dialog-header"><span class="section-kicker">{{ match.event === 'worlds' ? '2026 WORLDS' : '2026 MSI' }}</span><button class="icon-button" aria-label="닫기" @click="emit('close')">×</button></div>
    <h2 id="bet-title">{{ success ? '예측에 참여했습니다' : existing ? '내 베팅' : open ? '누가 승리할까요?' : '베팅이 마감되었습니다' }}</h2>
    <p class="muted small-text">{{ dateTime(match.startsAt) }} · {{ match.round }}</p>
    <template v-if="existing"><div class="bet-success"><TeamMark :team="existing.team" /><strong>{{ teams[existing.team].name }} 승리</strong><span>{{ points(existing.amount) }}</span><p class="muted small-text">결과 확정 전 · 정산 대기</p></div><p v-if="success" role="status" class="success-text">베팅 내역이 저장되었습니다.</p><button class="button primary full" @click="emit('close')">확인</button></template>
    <template v-else-if="open">
      <form @submit.prevent="submit">
        <div class="bet-team-options"><button v-for="team in match.teams" :key="team" type="button" :class="{ selected: selected === team }" :aria-pressed="selected === team" @click="selected = team"><TeamMark :team="team" /><strong>{{ teams[team].name }}</strong><span>{{ selected === team ? '선택됨' : '승리 예측' }}</span></button></div>
        <div class="amount-label"><label for="bet-amount">베팅 금액</label><span>보유 {{ points(game.points) }}</span></div>
        <div class="amount-input"><input id="bet-amount" v-model="amount" type="number" min="1" :max="game.points" step="1" required inputmode="numeric" /><span>P</span></div>
        <div class="quick-amounts"><button v-for="value in [1000, 5000, 10000, 50000]" :key="value" type="button" :disabled="value > game.points" @click="amount = value">{{ points(value) }}</button></div>
        <div class="estimate"><span>예상 배당 <b>{{ estimate.toFixed(2) }}×</b></span><span>예상 수령액 <b>{{ points(valid ? Math.floor(Number(amount) * estimate) : 0) }}</b></span></div>
        <p class="muted small-text">최종 배당은 베팅 마감 시 결정됩니다. 한 경기에는 한 팀만 선택할 수 있습니다.</p>
        <p v-if="betrayal" class="warning" role="status">{{ game.contract!.team }}의 상대 팀에 베팅합니다. 확정하면 이번 이벤트의 응원 인장이 박탈되며 복구할 수 없습니다.</p>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <button type="submit" class="button primary full" :disabled="!valid">{{ teams[selected].name }} 승리에 {{ points(Number(amount) || 0) }} 베팅</button>
      </form>
    </template>
    <template v-else><div class="closed-score"><TeamMark :team="match.teams[0]" /><strong>{{ match.score ? `${match.score[0]} : ${match.score[1]}` : 'VS' }}</strong><TeamMark :team="match.teams[1]" /></div><p class="muted">{{ match.winner ? `${teams[match.winner].name} 승리 · 샘플 경기 결과` : '경기 시작 이후에는 신규 베팅을 할 수 없습니다.' }}</p><button class="button secondary full" @click="emit('close')">닫기</button></template>
  </dialog>
</template>
