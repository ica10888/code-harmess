<script setup>
// 输入框下方的 markdown 实时预览：随输入重新渲染，只读，不影响发送的内容（发送的仍是 markdown 原文）。
import { computed } from 'vue'
import { renderMarkdown } from '../utils/markdown'

const props = defineProps({ text: { type: String, default: '' } })
const html = computed(() => renderMarkdown(props.text))
</script>

<template>
  <!-- html 来自 renderMarkdown：原始 HTML 已被转义，所以这里用 v-html 是安全的 -->
  <div v-if="html" class="preview" aria-label="Markdown 预览" v-html="html" />
</template>

<style scoped>
.preview {
  max-height: 160px;
  overflow-y: auto;
  padding: 8px 12px;
  border: 1px dashed #dcdfe6;
  border-radius: 4px;
  background: #fafafa;
  font-size: 14px;
  line-height: 1.5;
  word-break: break-word;
}
.preview :deep(> :first-child) {
  margin-top: 0;
}
.preview :deep(> :last-child) {
  margin-bottom: 0;
}
.preview :deep(h1),
.preview :deep(h2),
.preview :deep(h3) {
  margin: 8px 0;
  font-size: 16px;
}
.preview :deep(p) {
  margin: 4px 0;
}
.preview :deep(ul),
.preview :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
.preview :deep(code) {
  padding: 1px 4px;
  border-radius: 3px;
  background: #ececec;
  font-family: Consolas, 'Courier New', monospace;
}
.preview :deep(pre) {
  margin: 4px 0;
  padding: 8px;
  border-radius: 4px;
  background: #ececec;
  overflow-x: auto;
}
.preview :deep(pre code) {
  padding: 0;
  background: none;
}
.preview :deep(blockquote) {
  margin: 4px 0;
  padding-left: 8px;
  border-left: 3px solid #dcdfe6;
  color: #606266;
}
.preview :deep(a) {
  color: #409eff;
}
</style>
