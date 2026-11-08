<script setup>
// eureka 时刻的提示条：显示在模型那条回复的下面。文本来自后端（config.ini 的 [eureka] text），这里只负责展示。
// 它不是消息内容，也不会发给模型。后面接入新的实现时，在这里加交互即可。
defineProps({ text: { type: String, required: true } })
</script>

<template>
  <div class="eureka" role="status" aria-live="polite">{{ text }}</div>
</template>

<style scoped>
.eureka {
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
  /* 只在出现的那一下动一次，吸引注意；用户设置了「减少动效」就不动 */
  animation: eureka-pop 0.5s ease-out;
}
@keyframes eureka-pop {
  0% {
    transform: scale(0.92);
    opacity: 0;
  }
  60% {
    transform: scale(1.03);
    opacity: 1;
  }
  100% {
    transform: scale(1);
  }
}
@media (prefers-reduced-motion: reduce) {
  .eureka {
    animation: none;
  }
}
@media (max-width: 768px) {
  .eureka {
    max-width: 90%;
  }
}
</style>
