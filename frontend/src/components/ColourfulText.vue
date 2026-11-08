<script setup>
// 来自 Inspira UI 的 Colourful Text（https://inspira-ui.com），去掉了 TS 类型；依赖 motion-v
// 逐字做颜色渐变 + 轻微上下浮动，每隔几秒重新洗牌一次颜色
import { onMounted, onUnmounted, ref } from 'vue'
import { motion } from 'motion-v'

const props = defineProps({
  text: { type: String, required: true },
  colors: {
    type: Array,
    default: () => ['#4f8cff', '#7c5cff', '#c04cfd', '#ff5c93', '#ff8a4c', '#2fc36a'],
  },
  startColor: { type: String, default: '#ffffff' },
  duration: { type: Number, default: 0.5 },
})

const currentColors = ref(props.colors)
const count = ref(0) // 变一次，所有字重新播放一遍入场动画
let timer

onMounted(() => {
  timer = setInterval(() => {
    currentColors.value = [...props.colors].sort(() => 0.5 - Math.random())
    if (document.visibilityState === 'visible') count.value++
  }, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <motion.span
    v-for="(char, index) in props.text"
    :key="`${char}-${count}-${index}`"
    :initial="{ y: 0, opacity: 0.2, color: props.startColor, filter: 'blur(5px)' }"
    :transition="{ duration: props.duration, delay: index * 0.05 }"
    :animate="{
      y: [0, -3, 0],
      opacity: [1, 0.85, 1],
      filter: ['blur(0px)', 'blur(2px)', 'blur(0px)'],
      color: currentColors[index % currentColors.length],
    }"
    style="display: inline-block; white-space: pre"
  >
    {{ char }}
  </motion.span>
</template>
