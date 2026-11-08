<script setup>
// 输入框下方的「+」按钮：选一个本地文件，但「不立即上传」——只是挑好，显示在旁边（可移除）。
// 真正的上传由父组件在点「发送」时做：先传文件，传完再发文本（见 utils/sendFlow.js）。
// 只接受文件，不接受文件夹：点 + 号弹出的选择框本来就选不了文件夹；拖进来的会检查并拒绝。
import { NButton, NIcon, NTooltip, NUpload } from 'naive-ui'
import { Add, Close } from '@vicons/ionicons5'
import { message } from '../utils/feedback'
import { checkUpload, formatSize } from '../utils/upload'
import { isDirectoryEntry } from '../utils/sendFlow'

// v-model：当前选好的 File（没选是 null）
const file = defineModel({ type: Object, default: null })
defineProps({ disabled: { type: Boolean, default: false } })

// before-upload 返回 false：n-upload 不会自己去传；这里只做检查并记下文件
function onPick({ file: { file: rawFile } }) {
  const problem = checkUpload(rawFile)
  if (problem) message.warning(problem)
  else file.value = rawFile // 重新选一个就替换掉旧的（一次只带一个文件）
  return false
}

// 拖拽：浏览器允许把文件夹拖进页面，这里识别出来拒绝。
// 必须同步读 DataTransferItem（事件结束后就失效），所以不能放到 await 之后。
function onDrop(event) {
  const items = [...(event.dataTransfer?.items ?? [])]
  if (items.some((item) => isDirectoryEntry(item.webkitGetAsEntry?.()))) {
    event.preventDefault()
    event.stopPropagation()
    message.warning('不支持上传文件夹，请选择单个文件')
  }
}
</script>

<template>
  <div class="picker" @drop.capture="onDrop">
    <!-- 点按钮选文件，也可以把文件拖到按钮上（n-upload 的触发区自带拖拽） -->
    <n-upload
      :show-file-list="false"
      :default-upload="false"
      :disabled="disabled"
      :multiple="false"
      :directory="false"
      class="upload"
      @before-upload="onPick"
    >
      <n-tooltip placement="top">
        <template #trigger>
          <n-button class="square" :disabled="disabled" aria-label="选择文件">
            <template #icon><n-icon class="add-icon"><Add /></n-icon></template>
          </n-button>
        </template>
        选择文件（仅文件，不支持文件夹）
      </n-tooltip>
    </n-upload>

    <span v-if="file" class="chosen" :title="file.name">
      <span class="name">{{ file.name }}</span>
      <span class="size">{{ formatSize(file.size) }}</span>
      <n-button text :disabled="disabled" aria-label="移除文件" @click="file = null">
        <template #icon><n-icon class="close-icon"><Close /></n-icon></template>
      </n-button>
    </span>
  </div>
</template>

<style scoped>
.picker {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.upload {
  display: inline-flex;
  flex-shrink: 0; /* 「+」按钮不被压扁；要让位的是旁边的文件名 */
}
/* 方形按钮：宽高相等、只放一个图标；高度沿用 n-button 默认的 34px，和旁边的「发送」按钮一样高 */
.square {
  width: 34px;
  padding: 0;
  flex-shrink: 0; /* 空间再紧也不能把「+」压扁 */
}
/* 「+」悬停转 90° 并发光，按下缩小；移除的「×」悬停转 90° */
.add-icon,
.close-icon {
  transition: transform 0.3s ease, filter 0.25s ease;
}
.square:hover .add-icon {
  transform: rotate(90deg) scale(1.25);
  filter: drop-shadow(0 0 5px #3d6b8c);
}
.square:active .add-icon {
  transform: rotate(90deg) scale(0.8);
}
.chosen :deep(.n-button:hover) .close-icon {
  transform: rotate(90deg) scale(1.2);
}
@media (prefers-reduced-motion: reduce) {
  .add-icon,
  .close-icon {
    transition: none;
  }
  .square:hover .add-icon,
  .square:active .add-icon,
  .chosen :deep(.n-button:hover) .close-icon {
    transform: none;
  }
}
.chosen {
  flex: 0 1 auto; /* 可以收缩 */
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 0 8px;
  height: 34px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #fafafa;
  font-size: 13px;
}
.chosen .name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 220px;
  min-width: 0; /* 空间不够时文件名省略（…），让位给右边的控件，一行放不下就不会换行 */
}
.chosen .size {
  flex-shrink: 0;
  color: #909399;
  white-space: nowrap;
}
/* 输入区窄（两侧栏把中间栏挤窄，或手机）：这一排要和统计 / 模型 / 强度 / 发送挤在一行，所以文件标签收成紧凑形态：
   藏掉文件大小，文件名最多留 40px（超出省略，完整文件名在 title 里），✕ 始终可见，仍可移除 */
@container composer (max-width: 500px) {
  .chosen {
    padding: 0 6px;
    gap: 4px;
  }
  .chosen .size {
    display: none;
  }
  .chosen .name {
    max-width: 40px;
  }
}
/* 手机和极窄的输入区：选好的文件只留「✕」，文件名放不下就不显示（完整文件名在标签的 title 里，点 ✕ 可移除） */
@container composer (max-width: 420px) {
  .chosen .name {
    display: none;
  }
  .chosen {
    flex-shrink: 0; /* 只剩 ✕ 时是固定的小方块，不被压成窄条 */
    justify-content: center;
    width: 34px;
    padding: 0;
  }

}
</style>
