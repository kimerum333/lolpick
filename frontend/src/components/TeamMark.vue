<script setup lang="ts">
import { teams, type TeamId } from '../domain/game'
defineProps<{ team: TeamId; small?: boolean }>()
</script>
<template>
  <span class="team-mark" :class="{ small }" :style="{ '--team-color': teams[team].color }" :aria-label="teams[team].name">{{ team }}</span>
</template>
