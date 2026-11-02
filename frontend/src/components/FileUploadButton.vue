<script setup>
// 输入框下方的「+」按钮：选一个本地文件，但「不立即上传」——只是挑好，显示在旁边（可移除）。
// 真正的上传由父组件在点「发送」时做：先传文件，传完再发文本（见 utils/sendFlow.js）。
// 只接受文件，不接受文件夹：点 + 号弹出的选择框本来就选不了文件夹；拖进来的会检查并拒绝。
import { Close, Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { checkUpload, formatSize } from '../utils/upload'
import { isDirectoryEntry } from '../utils/sendFlow'

// v-model：当前选好的 File（没选是 null）
const file = defineModel({ type: Object, default: null })
defineProps({ disabled: { type: Boolean, default: false } })

// before-upload 返回 false：el-upload 不会自己去传；这里只做检查并记下文件
function onPick(rawFile) {
  const problem = checkUpload(rawFile)
  if (problem) ElMessage.warning(problem)
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
    ElMessage.warning('不支持上传文件夹，请选择单个文件')
  }
}
</script>

<template>
  <div class="picker" @drop.capture="onDrop">
    <el-upload
      :show-file-list="false"
      :auto-upload="true"
      :before-upload="onPick"
      :disabled="disabled"
      :multiple="false"
      :directory="false"
      drag
      class="upload"
    >
      <el-tooltip content="选择文件（仅文件，不支持文件夹）" placement="top">
        <el-button class="square" :disabled="disabled" aria-label="选择文件">
          <el-icon><Plus /></el-icon>
        </el-button>
      </el-tooltip>
    </el-upload>

    <span v-if="file" class="chosen" :title="file.name">
      <span class="name">{{ file.name }}</span>
      <span class="size">{{ formatSize(file.size) }}</span>
      <el-button link :disabled="disabled" aria-label="移除文件" @click="file = null">
        <el-icon><Close /></el-icon>
      </el-button>
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
}
/* 去掉 drag 模式默认的大虚线框，只留一个按钮；拖拽仍然有效 */
.upload :deep(.el-upload-dragger) {
  display: inline-flex;
  padding: 0;
  border: none;
  background: none;
  width: auto;
  height: auto;
}
/* 方形按钮：宽高相等、只放一个图标；高度沿用 el-button 默认的 32px，和旁边的「发送」按钮一样高 */
.square {
  width: 32px;
  padding: 0;
}
.chosen {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 0 8px;
  height: 32px;
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
}
.chosen .size {
  color: #909399;
  white-space: nowrap;
}
</style>
