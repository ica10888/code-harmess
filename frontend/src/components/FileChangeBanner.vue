<script setup>
// 文件变更提示条（EurekaBanner 的一份拷贝）：显示在模型那条回复的下面。文本和文件列表来自后端，这里只负责展示。
// 它不是消息内容，也不会发给模型。点一个文件，通知父组件跳转到工作区打开它。
// 特效（借鉴 Inspira UI 的 Tracing Beam / Animated List）：一道蓝色扫描线自上而下扫过整条提示；
// 文件行依次从左滑入，「新建」「修改」带小徽标，悬停时蓝色底色从左向右铺开、箭头滑出。
import { NIcon } from 'naive-ui'
import { AddCircleOutline, ArrowForwardOutline, CreateOutline, DocumentTextOutline } from '@vicons/ionicons5'

defineEmits(['open-file'])
defineProps({
  text: { type: String, required: true },
  files: { type: Array, default: () => [] },
})
</script>

<template>
  <div class="file-change" role="status" aria-live="polite">
    <div class="head"><n-icon class="doc" :size="18"><DocumentTextOutline /></n-icon>{{ text }}</div>
    <ul v-if="files.length" class="files">
      <li
        v-for="(f, i) in files"
        :key="f.path"
        class="file"
        :style="{ animationDelay: `calc(var(--gap, 0s) + ${0.25 + i * 0.1}s)` }"
        title="在工作区打开"
        @click="$emit('open-file', f.path)"
      >
        <span class="badge" :class="f.kind">
          <n-icon :size="12"><component :is="f.kind === 'created' ? AddCircleOutline : CreateOutline" /></n-icon>
          {{ f.kind === 'created' ? '新建' : '修改' }}
        </span>
        <span class="path">{{ f.path }}</span>
        <n-icon class="go" :size="14"><ArrowForwardOutline /></n-icon>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.file-change {
  position: relative;
  overflow: hidden; /* 扫描线不能跑出圆角 */
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
  animation: change-drop 0.5s cubic-bezier(0.2, 0.9, 0.3, 1.2) var(--gap, 0s) backwards;
}
/* 扫描线：入场时自上而下扫一遍，扫完留在底部变成一条淡蓝底边（forwards 保留） */
.file-change::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, #409eff, #a0cfff, #409eff, transparent);
  box-shadow: 0 0 10px 2px rgba(64, 158, 255, 0.7);
  pointer-events: none;
  animation: scan-down 1s ease-in-out calc(var(--gap, 0s) + 0.1s) both;
}
.head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.doc {
  animation: doc-flip 0.8s ease-out var(--gap, 0s) backwards;
}
.files {
  margin: 6px 0 0;
  padding: 0;
  list-style: none;
  font-weight: 400;
  font-size: 13px;
}
.file {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 2px -6px;
  padding: 2px 6px;
  border-radius: 4px;
  cursor: pointer;
  overflow: hidden;
  animation: file-in 0.4s ease-out backwards;
}
/* 悬停：蓝色底色从左向右铺开 */
.file::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  background: rgba(64, 158, 255, 0.14);
  transform: scaleX(0);
  transform-origin: left;
  transition: transform 0.3s ease;
}
.file:hover::before {
  transform: scaleX(1);
}
.badge,
.path,
.go {
  position: relative;
  z-index: 1;
}
.badge {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}
.badge.created {
  background: #e1f3d8;
  color: #529b2e;
  animation: badge-pulse 2s ease-in-out infinite;
}
.badge.modified {
  background: #fdf1d9;
  color: #b88230;
}
.path {
  min-width: 0;
  overflow-wrap: anywhere;
}
.go {
  margin-left: auto;
  opacity: 0;
  transform: translateX(-8px);
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.file:hover .go {
  opacity: 1;
  transform: none;
}
@keyframes change-drop {
  0% {
    transform: translateY(-14px);
    opacity: 0;
  }
  100% {
    transform: none;
    opacity: 1;
  }
}
@keyframes scan-down {
  0% {
    top: 0;
    opacity: 1;
  }
  85% {
    opacity: 1;
  }
  100% {
    top: calc(100% - 2px);
    opacity: 0.35;
    box-shadow: none;
  }
}
@keyframes doc-flip {
  0% {
    transform: rotateY(90deg) scale(0.6);
  }
  100% {
    transform: none;
  }
}
@keyframes file-in {
  0% {
    transform: translateX(-16px);
    opacity: 0;
  }
}
@keyframes badge-pulse {
  50% {
    box-shadow: 0 0 0 3px rgba(103, 194, 58, 0.25);
  }
}
@media (prefers-reduced-motion: reduce) {
  .file-change,
  .file-change::after,
  .doc,
  .file,
  .badge {
    animation: none;
  }
  .file-change::after {
    top: calc(100% - 2px);
    opacity: 0.35;
    box-shadow: none;
  }
  .file::before,
  .go {
    transition: none;
  }
}
@media (max-width: 768px) {
  .file-change {
    max-width: 90%;
  }
}
</style>
