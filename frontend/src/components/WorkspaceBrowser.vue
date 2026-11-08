<script setup>
// 工作区标签页：左边目录树，右边打开即可编辑的代码框（Prism 高亮，Ctrl+S 保存覆盖原文件）。路径由后端固定为本会话的工作区。
import { ref, watch, computed, h, onBeforeUnmount } from 'vue'
import { NButton, NIcon, NTree } from 'naive-ui'
import {
  CloudUploadOutline,
  CodeSlashOutline,
  DocumentTextOutline,
  DownloadOutline,
  FolderOpenOutline,
  FolderOutline,
  ImageOutline,
  MusicalNotesOutline,
  SaveOutline,
  VideocamOutline,
} from '@vicons/ionicons5'
import { confirm, message } from '../utils/feedback'
import { listWorkspaceFiles, readWorkspaceFile, writeWorkspaceFile, workspaceMediaUrl, workspaceDownloadUrl, uploadFile } from '../api'
import { PrismEditor } from 'vue-prism-editor'
import 'vue-prism-editor/dist/prismeditor.min.css'
import 'prismjs/themes/prism.css'
import { highlightFile } from '../utils/highlight'
import { buildTree } from '../utils/workspaceTree'
import { mediaKind } from '../utils/mediaKind'
import { checkUpload, uploadErrorMessage } from '../utils/upload'
import { isDirectoryEntry } from '../utils/sendFlow'

// target：外部要求打开的文件 { path }；每次都传新对象，重复点同一个文件也会重新打开
const props = defineProps({ sessionId: { type: String, default: null }, target: { type: Object, default: null } })

const tree = ref([])
const current = ref(null) // { path, content, truncated }；图片/视频/音频没有 content，多一个 media: { kind, url }
const draft = ref('') // 编辑框里的内容；打开文件时等于文件内容
const saving = ref(false)

// PrismEditor 的 highlight 回调：返回高亮后的 HTML，和只读查看用同一套逻辑
const highlight = (code) => highlightFile(current.value?.path ?? '', code)
const dirty = computed(() => !!current.value && draft.value !== current.value.content)

const REFRESH_MS = 3000 // 自动刷新间隔：模型随时可能在改工作区里的文件
let refreshing = false // 上一次还没回来就不再发，免得请求堆积

/** 静默刷新目录；当前文件没有未保存的修改时，内容也跟着磁盘更新。轮询失败不弹提示，下一轮再试。 */
async function refresh() {
  const sessionId = props.sessionId
  if (!sessionId || refreshing) return
  refreshing = true
  try {
    const files = await listWorkspaceFiles(sessionId)
    if (sessionId !== props.sessionId) return // 请求期间切了会话
    tree.value = buildTree(files)
    const open = current.value
    if (open && !open.media && !dirty.value && files.some((f) => f.path === open.path)) {
      const fresh = await readWorkspaceFile(sessionId, open.path)
      // 读的期间用户可能切了文件或开始输入，这时丢掉结果
      if (current.value === open && !dirty.value && fresh.content !== open.content) {
        current.value = fresh
        draft.value = fresh.content
      }
    }
  } catch {
    // 忽略：下一轮自动重试
  } finally {
    refreshing = false
  }
}

const timer = setInterval(refresh, REFRESH_MS)
onBeforeUnmount(() => clearInterval(timer))

/** 有未保存的修改时，先问一下再丢掉。 */
async function confirmDiscard() {
  if (!dirty.value) return true
  return confirm({ title: '提示', content: '当前文件有未保存的修改，放弃吗？' })
}

async function openFile(path) {
  if (!(await confirmDiscard())) return
  const kind = mediaKind(path)
  if (kind) {
    // 图片/视频/音频不读文本，直接让浏览器按地址加载
    current.value = { path, content: '', media: { kind, url: workspaceMediaUrl(props.sessionId, path) } }
    draft.value = ''
    return
  }
  try {
    current.value = await readWorkspaceFile(props.sessionId, path)
    draft.value = current.value.content
  } catch {
    message.error('读取文件失败')
  }
}

/** 下载当前文件：用一个临时的 <a download> 触发浏览器保存。 */
function download() {
  const a = document.createElement('a')
  a.href = workspaceDownloadUrl(props.sessionId, current.value.path)
  a.download = current.value.path.split('/').pop()
  a.click()
}

// 拖拽上传：落在目录节点上传到该目录，落在文件上传到它所在目录，其他地方传到根目录（重名会自动改名）；不支持拖入文件夹
/** 落点元素 → 目标子目录（相对工作区，根目录是空串）。 */
function dropDir(target) {
  const node = target instanceof Element ? target.closest('[data-path]') : null
  if (!node) return ''
  const path = node.dataset.path
  return node.dataset.dir ? path : path.split('/').slice(0, -1).join('/')
}
const dragging = ref(false)
const uploading = ref(false)
let dragDepth = 0 // dragenter/leave 会在子元素间反复触发，用计数判断是否真的离开

const hasFiles = (e) => [...(e.dataTransfer?.types ?? [])].includes('Files')
function onDragEnter(e) {
  if (!hasFiles(e)) return
  dragDepth++
  dragging.value = true
}
function onDragLeave() {
  if (--dragDepth <= 0) dragging.value = false
}
async function onDrop(e) {
  dragDepth = 0
  dragging.value = false
  if (!props.sessionId || !hasFiles(e)) return
  // 必须同步读 DataTransfer，事件结束后就失效
  const items = [...(e.dataTransfer.items ?? [])]
  const files = [...e.dataTransfer.files]
  const dir = dropDir(e.target)
  if (items.some((item) => isDirectoryEntry(item.webkitGetAsEntry?.()))) {
    message.warning('不支持上传文件夹，请拖入单个文件')
    return
  }
  uploading.value = true
  try {
    for (const file of files) {
      const problem = checkUpload(file)
      if (problem) {
        message.warning(`${file.name}：${problem}`)
        continue
      }
      try {
        await uploadFile(props.sessionId, file, undefined, dir)
        message.success(`已上传 ${file.name}${dir ? `到 ${dir}` : ''}`)
      } catch (err) {
        message.error(`${file.name}：${uploadErrorMessage(err)}`)
      }
    }
  } finally {
    uploading.value = false
    refresh()
  }
}

/** 快捷键：Ctrl/Cmd+S 保存。 */
function onKeydown(e) {
  if (!current.value || !(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 's') return
  e.preventDefault() // 挡住浏览器的「保存网页」
  save()
}

async function save() {
  if (!dirty.value || saving.value || current.value.truncated) return
  saving.value = true
  try {
    await writeWorkspaceFile(props.sessionId, current.value.path, draft.value)
    current.value = { ...current.value, content: draft.value }
    message.success('已保存')
  } catch (e) {
    message.error(e.response?.status === 413 ? '文件太大，无法保存' : '保存失败')
  } finally {
    saving.value = false
  }
}

// n-tree 的节点属性：点叶子打开文件；点目录由 expand-on-click 展开
// data-path / data-dir 让拖拽上传能从落点元素找回是哪个节点
const nodeProps = ({ option }) => ({
  'data-path': option.path,
  'data-dir': option.children ? '1' : '',
  onClick: () => !option.children && openFile(option.path),
})
// 节点图标：目录（展开时是打开的文件夹）、图片 / 视频 / 音频、普通文件；悬停动效见样式里的 .node-icon
const FILE_ICONS = { image: ImageOutline, video: VideocamOutline, audio: MusicalNotesOutline }
function nodeIcon(option) {
  if (option.children) return expanded.value.includes(option.path) ? FolderOpenOutline : FolderOutline
  return FILE_ICONS[mediaKind(option.path)] || DocumentTextOutline
}
const renderLabel = ({ option }) =>
  h('span', { class: ['node', { active: option.path === current.value?.path, dir: !!option.children }] }, [
    h(NIcon, { class: 'node-icon', size: 16 }, { default: () => h(nodeIcon(option)) }),
    option.label,
  ])

// 目录树里展开的目录：一开始是外部指定文件的所有上级；之后由用户点开 / 收起（要知道谁展开了，才能换文件夹图标）
const expanded = ref(
  (() => {
    const parts = props.target?.path.split('/').slice(0, -1) ?? []
    return parts.map((_, i) => parts.slice(0, i + 1).join('/'))
  })(),
)

// 切会话：清掉旧会话的文件，重新读目录
watch(
  () => props.sessionId,
  () => {
    current.value = null
    draft.value = ''
    tree.value = []
    refresh()
  },
  { immediate: true },
)
// 放在切会话的 watch 之后：挂载时两个都会立刻触发，要先清空再打开
watch(() => props.target, (t) => t && openFile(t.path), { immediate: true })
</script>

<template>
  <div
    class="browser"
    :class="{ dragging }"
    tabindex="-1"
    @keydown="onKeydown"
    @dragenter.prevent="onDragEnter"
    @dragover.prevent
    @dragleave="onDragLeave"
    @drop.prevent="onDrop"
  >
    <Transition name="fade">
      <div v-if="dragging || uploading" class="drop-hint">
        <n-icon class="drop-icon" :size="28"><CloudUploadOutline /></n-icon>
        {{ uploading ? '上传中…' : '松开鼠标，上传到工作区' }}
      </div>
    </Transition>
    <div class="split">
      <div class="tree">
        <div class="bar">目录</div>
        <div v-if="!tree.length" class="empty">
          <n-icon class="float-icon" :size="36"><FolderOpenOutline /></n-icon>
          <div>工作区还没有文件</div>
        </div>
        <n-tree
          v-else
          :data="tree"
          key-field="path"
          label-field="label"
          block-line
          v-model:expanded-keys="expanded"
          :expand-on-click="true"
          :node-props="nodeProps"
          :render-label="renderLabel"
          :selectable="false"
        />
      </div>
      <div class="file">
        <div v-if="!current" class="empty">
          <n-icon class="float-icon" :size="48"><CodeSlashOutline /></n-icon>
          <div>点左边的文件打开</div>
        </div>
        <!-- 换文件时整块淡入上滑；key 用路径，所以每次打开新文件都会重新播放 -->
        <div v-else :key="current.path" class="opened">
          <div class="bar">
            <strong>{{ current.path }}</strong>
            <span class="actions">
              <n-button size="small" class="act download" @click="download">
                <template #icon><n-icon><DownloadOutline /></n-icon></template>
                下载
              </n-button>
              <n-button
                v-if="!current.media"
                size="small"
                type="primary"
                class="act save"
                :class="{ ready: dirty && !current.truncated }"
                :loading="saving"
                :disabled="!dirty || current.truncated"
                title="Ctrl+S"
                @click="save"
              >
                <template #icon><n-icon><SaveOutline /></n-icon></template>
                保存
              </n-button>
            </span>
          </div>
          <div v-if="current.media" class="media">
            <img v-if="current.media.kind === 'image'" :key="current.path" :src="current.media.url" :alt="current.path" />
            <video v-else-if="current.media.kind === 'video'" :key="current.path" :src="current.media.url" controls preload="metadata" />
            <audio v-else :key="current.path" :src="current.media.url" controls preload="metadata" />
          </div>
          <div v-if="current.truncated" class="tip">文件较大，只显示了前面一部分，只读</div>
          <!-- 打开即可编辑；Tab 缩进、Ctrl+Z / Ctrl+Y 撤销重做由组件自带；被截断的大文件只读 -->
          <prism-editor
            v-if="!current.media"
            :key="current.path"
            v-model="draft"
            class="editor"
            :highlight="highlight"
            :readonly="current.truncated"
            line-numbers
            :tab-size="4"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.browser {
  position: relative;
  container-type: inline-size; /* 按自身宽度（而不是屏幕）适配窄框；容器查询改不了容器自己，所以排版放在内层 .split */
  flex: 1;
  min-height: 0;
  font-size: 13px;
}
.split {
  display: flex;
  height: 100%;
}
.tree {
  width: 240px;
  flex-shrink: 0;
  overflow: auto;
  padding: 8px;
  border-right: 1px solid #e4e7ed;
}
.file {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 8px 12px;
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.tree :deep(.active) {
  color: #2f4a63;
  font-weight: 600;
}
.tree :deep(.node) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tree :deep(.node-icon) {
  color: #4f7f99;
  transition: transform 0.25s ease, filter 0.25s ease;
}
.tree :deep(.dir .node-icon) {
  color: #2f4a63;
}
.tree :deep(.n-tree-node:hover .node-icon) {
  transform: scale(1.25) rotate(-6deg);
  filter: drop-shadow(0 0 4px #3d6b8c);
}
.tree :deep(.n-tree-node:active .node-icon) {
  transform: scale(0.85);
}
/* 选中的文件：图标常亮冷蓝光晕 */
.tree :deep(.active .node-icon) {
  color: #3d6b8c;
  filter: drop-shadow(0 0 4px #3d6b8c);
}
.opened {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  animation: open-in 0.3s ease;
}
@keyframes open-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
/* 空状态图标缓慢上下浮动（Inspira UI 的 Float 思路） */
.float-icon {
  display: block;
  margin: 0 auto 8px;
  color: #4f7f99;
  animation: float 3s ease-in-out infinite;
}
@keyframes float {
  0%,
  100% {
    transform: translateY(0) rotate(-6deg);
  }
  50% {
    transform: translateY(-8px) rotate(6deg);
  }
}
/* 按钮图标：下载悬停时向下点一下，保存在有未保存修改时轻轻跳动提醒 */
.act :deep(.n-icon) {
  transition: transform 0.25s ease;
}
.download:hover :deep(.n-icon) {
  transform: translateY(2px) scale(1.15);
}
.save.ready :deep(.n-icon) {
  animation: nudge 1.4s ease-in-out infinite;
}
@keyframes nudge {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.25);
  }
}
@media (prefers-reduced-motion: reduce) {
  .float-icon,
  .opened,
  .drop-icon,
  .drop-hint,
  .save.ready :deep(.n-icon) {
    animation: none;
  }
  .tree :deep(.node-icon),
  .act :deep(.n-icon),
  .fade-enter-active,
  .fade-leave-active {
    transition: none;
  }
  .tree :deep(.n-tree-node:hover .node-icon),
  .tree :deep(.n-tree-node:active .node-icon),
  .download:hover :deep(.n-icon) {
    transform: none;
  }
}
.empty,
.tip {
  color: #909399;
  text-align: center;
  margin-top: 24px;
}
.tip {
  margin: 0 0 8px;
}
.media {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  background: #f6f8fa;
}
.media img,
.media video {
  max-width: 100%;
  max-height: 100%;
}
.media audio {
  width: 100%;
  max-width: 480px;
  margin: 0 8px;
}
.bar strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.editor {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  background: #f6f8fa;
  color: #24292f;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.5;
  padding: 5px;
}
/* 代码不换行：否则折行后和行号对不上。容器随最长的一行变宽，外层 .editor 横向滚动；行号随代码一起平移（不用 sticky，否则会漂浮盖在代码上） */
.editor :deep(.prism-editor__container) {
  width: max-content;
  min-width: 100%;
  overflow: visible;
}
.editor :deep(.prism-editor__editor),
.editor :deep(.prism-editor__textarea) {
  white-space: pre;
  overflow-wrap: normal;
}
.editor :deep(.prism-editor__line-numbers) {
  padding-right: 6px;
  margin-right: 4px;
}
.editor :deep(textarea:focus) {
  outline: none;
}
.actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.drop-hint {
  position: absolute;
  inset: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 8px;
  background: rgba(47, 74, 99, 0.12);
  border: 2px dashed #3d6b8c;
  color: #2f4a63;
  font-size: 16px;
  pointer-events: none; /* 不挡住 drop 事件 */
  animation: pulse-border 1.2s ease-in-out infinite;
}
.drop-icon {
  animation: drop-bounce 0.9s ease-in-out infinite;
}
@keyframes pulse-border {
  50% {
    border-color: #8fb3cc;
    background: rgba(47, 74, 99, 0.2);
  }
}
@keyframes drop-bounce {
  50% {
    transform: translateY(-6px);
  }
}
.browser:focus {
  outline: none;
}
@container (max-width: 520px) {
  /* 下载 / 保存按钮只留图标（文字用 .act 里 n-button 的 content 隐藏），把宽度让给文件名 */
  .act :deep(.n-button__content) {
    display: none;
  }
  .act :deep(.n-button__icon) {
    margin: 0;
  }
  .file {
    padding: 8px;
  }
  .split {
    flex-direction: column;
  }
  .tree {
    width: auto;
    max-height: 35%;
    border-right: none;
    border-bottom: 1px solid #e4e7ed;
  }
}
</style>
