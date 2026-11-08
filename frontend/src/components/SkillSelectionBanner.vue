<script setup>
// 选中 skill 的提示条（FileChangeBanner 的一份拷贝）：显示在模型那条回复的下面。文本和名字来自后端，这里只负责展示。
// 它不是消息内容，也不会发给模型。
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
      <div class="group-title">🧩 插件</div>
      <ul class="names">
        <li v-for="name in plugins" :key="name">{{ name }}</li>
      </ul>
    </div>
    <div v-if="workflows.length" class="group">
      <div class="group-title">🚀 工作流</div>
      <ul class="names">
        <li v-for="name in workflows" :key="name">{{ name }}</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.skill-selection {
  align-self: flex-start;
  max-width: 70%;
  margin-top: 6px;
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid #c2e7b0;
  background: linear-gradient(90deg, #f0f9eb, #f6fbf2);
  color: #529b2e;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  word-break: break-word;
  animation: skill-selection-pop 0.5s ease-out;
}
.group {
  margin-top: 6px;
}
.group-title {
  font-size: 13px;
}
.names {
  margin: 2px 0 0;
  padding-left: 18px;
  font-weight: 400;
  font-size: 13px;
}
@keyframes skill-selection-pop {
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
  .skill-selection {
    animation: none;
  }
}
@media (max-width: 768px) {
  .skill-selection {
    max-width: 90%;
  }
}
</style>
