<script setup>
// 流星雨背景：参考 Inspira UI 的 Meteors（https://inspira-ui.com），官方版依赖 Tailwind，这里用纯 CSS 重写，不新增依赖。
// 每颗流星是一个小圆点 + 一条渐隐的尾巴，沿 145° 方向（向左下）划过。
// 频率：每颗流星的动画周期是 PERIOD_S 秒，只在周期开头约 3 秒内划过，其余时间隐藏；
// 默认 2 颗、周期 60 秒、相差半个周期，所以平均一分钟划过 2 次。每划完一次，位置重新随机。
// 放在容器里 position:absolute 铺满，pointer-events:none，不挡住下面的操作；容器要 overflow:hidden。
import { ref } from 'vue'

const props = defineProps({
  count: { type: Number, default: 2 },
  period: { type: Number, default: 60 }, // 每颗流星的循环周期（秒）
})

const randomLeft = () => `${Math.round(Math.random() * 130 - 10)}%` // 超出右边一点：向左下划的流星，从右上方进入画面
const meteors = ref(
  Array.from({ length: props.count }, (_, i) => ({
    left: randomLeft(),
    // 第一颗几秒后出现，之后依次错开半个周期（负延迟 = 动画从中途开始）
    delay: `${(i * props.period) / props.count - props.period + 4}s`,
    tail: `${Math.round(80 + Math.random() * 60)}px`,
  })),
)
// 一圈划完（回到起点，已不可见）时换个位置
const reroll = (m) => (m.left = randomLeft())
</script>

<template>
  <div class="meteors" aria-hidden="true">
    <span
      v-for="(m, i) in meteors"
      :key="i"
      class="meteor"
      :style="{ left: m.left, animationDelay: m.delay, animationDuration: `${period}s`, '--tail': m.tail }"
      @animationiteration="reroll(m)"
    />
  </div>
</template>

<style scoped>
.meteors {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}
.meteor {
  position: absolute;
  top: -10px;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #3d6b8c;
  box-shadow: 0 0 6px 1px rgba(61, 107, 140, 0.6);
  transform: rotate(145deg);
  opacity: 0;
  animation: meteor 60s linear infinite;
}
/* 尾巴：在运动方向的反侧（旋转后的 -x 方向，即右上）；靠近圆点处亮，往后渐隐 */
.meteor::before {
  content: '';
  position: absolute;
  top: 50%;
  right: 100%;
  width: var(--tail, 100px);
  height: 1px;
  transform: translateY(-50%);
  background: linear-gradient(270deg, rgba(61, 107, 140, 0.8), transparent);
}
/* 一个周期 = 60 秒：前 5%（3 秒）是划过的过程，之后一直隐藏到周期结束 */
@keyframes meteor {
  0% {
    transform: rotate(145deg) translateX(0);
    opacity: 0;
  }
  0.5% {
    opacity: 1;
  }
  4% {
    opacity: 1;
  }
  5%,
  100% {
    transform: rotate(145deg) translateX(700px);
    opacity: 0;
  }
}
@media (max-width: 768px) {
  .meteor::before {
    width: calc(var(--tail, 100px) * 0.5); /* 手机屏窄，尾巴缩短一半 */
  }
}
@media (prefers-reduced-motion: reduce) {
  .meteors {
    display: none;
  }
}
</style>
