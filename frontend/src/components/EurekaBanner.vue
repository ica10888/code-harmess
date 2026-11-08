<script setup>
// eureka 时刻的提示条：显示在模型那条回复的下面。文本来自后端（config.ini 的 [eureka] text），这里只负责展示。
// 它不是消息内容，也不会发给模型。后面接入新的实现时，在这里加交互即可。
// 特效（借鉴 Inspira UI 的 Radiant Text / Sparkles Text）：灯泡亮起、金色高光扫过文字、周围星星迸发后常驻闪烁；
// 入场动画结束后，金色光晕和闪烁保留。
import { NIcon } from 'naive-ui'
import { BulbOutline, SparklesOutline } from '@vicons/ionicons5'

defineProps({ text: { type: String, required: true } })
// 星星的位置和闪烁延迟（百分比 / 秒）
const STARS = [
  { left: '4%', top: '-6px', delay: 0 },
  { left: '30%', top: '-8px', delay: 0.5 },
  { left: '62%', top: '-6px', delay: 0.2 },
  { left: '92%', top: '-4px', delay: 0.8 },
  { left: '78%', top: 'calc(100% - 8px)', delay: 0.35 },
  { left: '14%', top: 'calc(100% - 6px)', delay: 0.65 },
]
</script>

<template>
  <div class="eureka" role="status" aria-live="polite">
    <n-icon v-for="(s, i) in STARS" :key="i" class="star" :size="12" :style="{ left: s.left, top: s.top, animationDelay: `${s.delay}s` }">
      <SparklesOutline />
    </n-icon>
    <n-icon class="bulb" :size="20"><BulbOutline /></n-icon>
    <span class="text">{{ text }}</span>
  </div>
</template>

<style scoped>
.eureka {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  align-self: flex-start;
  max-width: 70%;
  margin-top: 6px;
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid #f3d19e;
  background: linear-gradient(90deg, #fdf6ec, #fff8e1);
  color: #b88230;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  /* 入场：弹出 + 金光爆闪，结束后停在柔和的金色光晕上（forwards 保留） */
  animation: eureka-pop 0.6s ease-out var(--gap, 0s) backwards, eureka-flash 1.4s ease-out var(--gap, 0s) both;
}
.text {
  /* 金色高光从左扫到右，循环；文字本身用渐变填充 */
  background: linear-gradient(110deg, #b88230 35%, #ffd76a 50%, #b88230 65%);
  background-size: 250% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: eureka-shine 2.6s linear calc(var(--gap, 0s) + 0.4s) infinite;
}
.bulb {
  flex-shrink: 0;
  margin-top: 2px;
  color: #e6a23c;
  /* 灯泡「啪」地亮起，之后缓慢呼吸 */
  animation: bulb-on 0.7s ease-out var(--gap, 0s) backwards, bulb-breathe 2.4s ease-in-out calc(var(--gap, 0s) + 0.7s) infinite;
}
.star {
  position: absolute;
  color: #f0b44c;
  opacity: 0;
  pointer-events: none;
  animation: star-twinkle 2.2s ease-in-out infinite;
}
@keyframes eureka-pop {
  0% {
    transform: scale(0.9);
    opacity: 0;
  }
  60% {
    transform: scale(1.04);
    opacity: 1;
  }
  100% {
    transform: scale(1);
  }
}
@keyframes eureka-flash {
  0% {
    box-shadow: 0 0 0 0 rgba(255, 215, 106, 0.9);
  }
  40% {
    box-shadow: 0 0 22px 6px rgba(255, 215, 106, 0.85);
  }
  100% {
    box-shadow: 0 0 10px 1px rgba(240, 180, 76, 0.35);
  }
}
@keyframes eureka-shine {
  to {
    background-position: -150% 0;
  }
}
@keyframes bulb-on {
  0% {
    transform: scale(0.4) rotate(-25deg);
    filter: grayscale(1) brightness(0.8);
  }
  60% {
    transform: scale(1.4) rotate(8deg);
    filter: drop-shadow(0 0 8px #ffd76a);
  }
  100% {
    transform: scale(1);
  }
}
@keyframes bulb-breathe {
  50% {
    transform: scale(1.12);
    filter: drop-shadow(0 0 6px #ffd76a);
  }
}
@keyframes star-twinkle {
  0%,
  100% {
    opacity: 0;
    transform: scale(0.3) rotate(0deg);
  }
  50% {
    opacity: 1;
    transform: scale(1.1) rotate(90deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .eureka,
  .text,
  .bulb {
    animation: none;
  }
  .text {
    background: none;
    -webkit-text-fill-color: currentColor;
  }
  .eureka {
    box-shadow: 0 0 10px 1px rgba(240, 180, 76, 0.35);
  }
  .star {
    animation: none;
    opacity: 0.7;
  }
}
@media (max-width: 768px) {
  .eureka {
    max-width: 90%;
  }
}
</style>
