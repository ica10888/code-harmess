<script setup>
// 等待模型回复时的占位：随机挑一个词，一个字一个字露出来，再露出 "..."（渐进式披露）；
// 露完停一会儿，换一个词从头再来，一直循环，直到父组件在第一段回复到来时移除它。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NIcon } from 'naive-ui'
import { SparklesOutline } from '@vicons/ionicons5'
import { pickWord, revealStep, totalSteps } from '../utils/waitingWord'

const STEP_MS = 70
const HOLD_STEPS = 10 // 露完整个词后停留的步数（约 0.7 秒），让人看清

const word = ref(pickWord())
const step = ref(0)
const text = computed(() => revealStep(word.value, step.value))
let timer = null

onMounted(() => {
  timer = setInterval(() => {
    if (step.value < totalSteps(word.value) + HOLD_STEPS) {
      step.value += 1
    } else {
      word.value = pickWord(Math.random, word.value) // 换一个不同的词，从头披露
      step.value = 0
    }
  }, STEP_MS)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="waiting" role="status" aria-live="polite">
    <n-icon class="spark" :size="16"><SparklesOutline /></n-icon>{{ text }}
  </div>
</template>

<style scoped>
.waiting {
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 70%;
  min-height: 1.5em;
  padding: 8px 16px;
  border-radius: 8px;
  background: #f4f4f5;
  color: #909399;
  font-style: italic;
  line-height: 1.5;
}
/* 闪烁图标：缓慢旋转并明暗呼吸，表示模型正在想 */
.spark {
  flex-shrink: 0;
  color: #4f7f99;
  animation: spark 1.6s ease-in-out infinite;
}
@keyframes spark {
  50% {
    transform: scale(1.3) rotate(20deg);
    opacity: 0.5;
  }
}
@media (prefers-reduced-motion: reduce) {
  .spark {
    animation: none;
  }
}
@media (max-width: 768px) {
  .waiting {
    max-width: 90%;
  }
}
</style>
