<script setup>
// 工作区标签页：左边目录树，右边打开即可编辑的代码框（Prism 高亮，Ctrl+S 保存覆盖原文件）。路径由后端固定为本会话的工作区。
import { ref, watch, computed, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listWorkspaceFiles, readWorkspaceFile, writeWorkspaceFile } from '../api'
import { PrismEditor } from 'vue-prism-editor'
import 'vue-prism-editor/dist/prismeditor.min.css'
import 'prismjs/themes/prism.css'
import { highlightFile } from '../utils/highlight'
import { buildTree } from '../utils/workspaceTree'

// target：外部要求打开的文件 { path }；每次都传新对象，重复点同一个文件也会重新打开
const props = defineProps({ sessionId: { type: String, default: null }, target: { type: Object, default: null } })

const tree = ref([])
const current = ref(null) // { path, content, truncated }
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
    if (open && !dirty.value && files.some((f) => f.path === open.path)) {
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
  try {
    await ElMessageBox.confirm('当前文件有未保存的修改，放弃吗？', '提示', { type: 'warning' })
    return true
  } catch {
    return false
  }
}

async function openFile(path) {
  if (!(await confirmDiscard())) return
  try {
    current.value = await readWorkspaceFile(props.sessionId, path)
    draft.value = current.value.content
  } catch {
    ElMessage.error('读取文件失败')
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
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(e.response?.status === 413 ? '文件太大，无法保存' : '保存失败')
  } finally {
    saving.value = false
  }
}

const open = (node) => !node.children && openFile(node.path)

// 目录树里要展开的目录：外部指定文件的所有上级（只在树第一次渲染时生效）
const expandedKeys = computed(() => {
  const parts = props.target?.path.split('/').slice(0, -1) ?? []
  return parts.map((_, i) => parts.slice(0, i + 1).join('/'))
})

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
  <div class="browser" tabindex="-1" @keydown="onKeydown">
    <div class="tree">
      <div class="bar">目录</div>
      <div v-if="!tree.length" class="empty">工作区还没有文件</div>
      <el-tree
        v-else
        :data="tree"
        node-key="path"
        :default-expanded-keys="expandedKeys"
        :expand-on-click-node="true"
        @node-click="open"
      >
        <template #default="{ data }">
          <span :class="{ active: data.path === current?.path }">{{ data.children ? '📁' : '📄' }} {{ data.label }}</span>
        </template>
      </el-tree>
    </div>
    <div class="file">
      <div v-if="!current" class="empty">点左边的文件打开</div>
      <template v-else>
        <div class="bar">
          <strong>{{ current.path }}</strong>
          <el-button size="small" type="primary" :loading="saving" :disabled="!dirty || current.truncated" title="Ctrl+S" @click="save">保存</el-button>
        </div>
        <div v-if="current.truncated" class="tip">文件较大，只显示了前面一部分，只读</div>
        <!-- 打开即可编辑；Tab 缩进、Ctrl+Z / Ctrl+Y 撤销重做由组件自带；被截断的大文件只读 -->
        <prism-editor
          :key="current.path"
          v-model="draft"
          class="editor"
          :highlight="highlight"
          :readonly="current.truncated"
          line-numbers
          :tab-size="4"
        />
      </template>
    </div>
  </div>
</template>

<style scoped>
.browser {
  flex: 1;
  display: flex;
  min-height: 0;
  font-size: 13px;
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
.active {
  color: #409eff;
  font-weight: 600;
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
.editor :deep(textarea:focus) {
  outline: none;
}
.browser:focus {
  outline: none;
}
@media (max-width: 768px) {
  .browser {
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
