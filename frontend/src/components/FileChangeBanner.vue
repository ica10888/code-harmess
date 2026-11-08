<script setup>
// 文件变更提示条（EurekaBanner 的一份拷贝）：显示在模型那条回复的下面。文本和文件列表来自后端，这里只负责展示。
// 它不是消息内容，也不会发给模型。点一个文件，通知父组件跳转到工作区打开它。
defineEmits(['open-file'])
defineProps({
  text: { type: String, required: true },
  files: { type: Array, default: () => [] },
})
</script>

<template>
  <div class="file-change" role="status" aria-live="polite">
    <div>{{ text }}</div>
    <ul v-if="files.length" class="files">
      <li v-for="f in files" :key="f.path" class="file" title="在工作区打开" @click="$emit('open-file', f.path)">
        {{ f.kind === 'created' ? '新建' : '修改' }}　{{ f.path }}
      </li>
    </ul>
  </div>
</template>

<style scoped>
.file-change {
  align-self: flex-start;
  max-width: 70%;
  margin-top: 6px;
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid #b3d8ff;
  background: linear-gradient(90deg, #ecf5ff, #f4f9ff);
  color: #337ecc;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  word-break: break-word;
  animation: file-change-pop 0.5s ease-out;
}
.file {
  cursor: pointer;
  text-decoration: underline;
}
.files {
  margin: 4px 0 0;
  padding-left: 18px;
  font-weight: 400;
  font-size: 13px;
}
@keyframes file-change-pop {
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
  .file-change {
    animation: none;
  }
}
</style>
