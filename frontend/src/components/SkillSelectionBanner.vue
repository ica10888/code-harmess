<script setup>
// 选中 skill 的提示条（FileChangeBanner 的一份拷贝）：显示在模型那条回复的下面。文本和名字来自后端，这里只负责展示。
// 它不是消息内容，也不会发给模型。
// 特效（借鉴 Inspira UI 的 Border Beam / Animated List）：一道绿光沿边框持续环绕，插件和工作流的名字做成芯片，
// 依次弹出；图标各有动作（拼图块轻晃、火箭轻轻起飞）。
import { NIcon } from 'naive-ui'
import { ExtensionPuzzleOutline, RocketOutline } from '@vicons/ionicons5'

defineProps({
  text: { type: String, required: true },
  plugins: { type: Array, default: () => [] },
  workflows: { type: Array, default: () => [] },
})
</script>

<template>
  <div class="skill-selection" role="status" aria-live="polite">
    <div>{{ text }}</div>
    <div v-if="plugins.length" class="group">
      <div class="group-title"><n-icon class="plug"><ExtensionPuzzleOutline /></n-icon>插件</div>
      <ul class="names">
        <li v-for="(name, i) in plugins" :key="name" class="chip" :style="{ animationDelay: `calc(var(--gap, 0s) + ${0.3 + i * 0.12}s)` }">{{ name }}</li>
      </ul>
    </div>
    <div v-if="workflows.length" class="group">
      <div class="group-title"><n-icon class="rocket"><RocketOutline /></n-icon>工作流</div>
      <ul class="names">
        <li v-for="(name, i) in workflows" :key="name" class="chip" :style="{ animationDelay: `calc(var(--gap, 0s) + ${0.3 + (plugins.length + i) * 0.12}s)` }">{{ name }}</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
/* 光束的角度：注册成角度类型，才能被动画插值（不支持的浏览器里光束不转，边框仍是静态的） */
@property --beam-angle {
  syntax: '<angle>';
  inherits: false;
  initial-value: 0deg;
}
.skill-selection {
  position: relative;
  align-self: flex-start;
  max-width: 70%;
  margin-bottom: 6px; /* 提示条在回复气泡的上面（skill_selector 在回复之前触发） */
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid #c2e7b0;
  background: linear-gradient(90deg, #f0f9eb, #f6fbf2);
  color: #529b2e;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  word-break: break-word;
  animation: skill-slide 0.55s cubic-bezier(0.2, 0.9, 0.3, 1.2) var(--gap, 0s) backwards;
}
/* 环绕边框的光束：只显示 2px 宽的边（mask 挖空中间），一段亮绿渐变随角度转圈 */
.skill-selection::before {
  content: '';
  position: absolute;
  inset: -1px;
  padding: 2px;
  border-radius: 8px;
  background: conic-gradient(from var(--beam-angle), transparent 0 70%, #67c23a 85%, #b7f08f 95%, transparent 100%);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0);
  pointer-events: none;
  animation: beam-spin 3.2s linear infinite;
  animation-delay: var(--gap, 0s);
}
.group {
  margin-top: 6px;
}
.group-title {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
}
.names {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
  font-weight: 400;
  font-size: 13px;
}
/* 名字芯片：依次从小弹到大；悬停时上浮 */
.chip {
  padding: 1px 10px;
  border-radius: 999px;
  border: 1px solid #b7e0a0;
  background: #fff;
  animation: chip-pop 0.4s cubic-bezier(0.2, 0.9, 0.3, 1.4) backwards;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.chip:hover {
  transform: translateY(-2px);
  box-shadow: 0 3px 8px rgba(103, 194, 58, 0.35);
}
.plug {
  animation: plug-wiggle 2.8s ease-in-out infinite;
}
.rocket {
  animation: rocket-fly 2.2s ease-in-out infinite;
}
@keyframes skill-slide {
  0% {
    transform: translateX(-24px) scale(0.96);
    opacity: 0;
  }
  100% {
    transform: none;
    opacity: 1;
  }
}
@keyframes beam-spin {
  to {
    --beam-angle: 360deg;
  }
}
@keyframes chip-pop {
  0% {
    transform: scale(0);
    opacity: 0;
  }
}
@keyframes plug-wiggle {
  0%,
  70%,
  100% {
    transform: rotate(0);
  }
  80% {
    transform: rotate(-14deg);
  }
  90% {
    transform: rotate(10deg);
  }
}
@keyframes rocket-fly {
  0%,
  100% {
    transform: translate(0, 0);
  }
  50% {
    transform: translate(2px, -3px);
  }
}
@media (prefers-reduced-motion: reduce) {
  .skill-selection,
  .skill-selection::before,
  .chip,
  .plug,
  .rocket {
    animation: none;
  }
  .chip {
    transition: none;
  }
  .chip:hover {
    transform: none;
  }
}
@media (max-width: 768px) {
  .skill-selection {
    max-width: 90%;
  }
}
</style>
