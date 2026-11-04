<script setup>
// 页面最右侧的插件框（也用来做它下方的工作流框：kind="workflow"，逻辑相同，只是文案和接口不同，且上传不检查 frontmatter）：竖向复选框列表，每个框对应一个插件（skill）；下方两个按钮：增加、删除。
// 插件属于「当前会话」：后端把它们存在这个会话自己的目录里，切换会话就换一份列表。
//   勾选 / 取消勾选：立即保存到后端。
//   增加：选一个 .md 文件上传，文件名就是插件名。文件开头必须有 frontmatter（name + description）：
//   勾选后后端要读它，把全文注入对话。选好后先在浏览器里检查，不合格就提示，不会白传。
//   删除：删除当前「已勾选」的插件（先二次确认）。默认插件（后端 config.ini 配置的目录里的）所有会话都有、不能删除，
//   可以勾选但不会被删除，旁边带「默认」标记。
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  addPlugin,
  addWorkflow,
  deletePlugin,
  deleteWorkflow,
  getPlugin,
  getWorkflow,
  listPlugins,
  listWorkflows,
  setPluginEnabled,
  setWorkflowEnabled,
} from '../api'
import {
  checkSkillContent,
  checkSkillFile,
  deletablePlugins,
  pluginErrorMessage,
  readHead,
  removeByName,
  replaceByName,
} from '../utils/plugins'

// 当前会话的 id；还没有会话时是 null，插件框显示为空、按钮不可点
// kind：'plugin'（插件，默认）或 'workflow'（工作流）
const props = defineProps({
  sessionId: { type: String, default: null },
  kind: { type: String, default: 'plugin' },
})

const isWorkflow = props.kind === 'workflow'
const noun = isWorkflow ? '工作流' : '插件'
const api = isWorkflow
  ? { list: listWorkflows, get: getWorkflow, add: addWorkflow, setEnabled: setWorkflowEnabled, remove: deleteWorkflow }
  : { list: listPlugins, get: getPlugin, add: addPlugin, setEnabled: setPluginEnabled, remove: deletePlugin }

const plugins = ref([])
const loading = ref(false)
// 已勾选的数量变了就告诉父组件：聊天页据此判断「没写文字但勾了插件」时能不能发送
const emit = defineEmits(['checked', 'toggled'])
watch(
  plugins,
  (list) => emit('checked', list.filter((p) => p.enabled).length),
  { deep: true },
)
const busy = ref(false) // 增加 / 删除进行中，期间按钮和复选框都不可点，免得并发操作

// 删除按钮只看「已勾选且不是默认」的：默认插件勾了也不会被删
const deletable = computed(() => deletablePlugins(plugins.value))

// 会话变了就重新加载。快速连续切换时，先发出的请求可能后返回：只认「最后一次切换」对应的结果
let loadToken = 0
watch(
  () => props.sessionId,
  async (id) => {
    const token = ++loadToken
    plugins.value = []
    if (!id) return
    loading.value = true
    try {
      const list = await api.list(id)
      if (token === loadToken) plugins.value = list
    } catch (err) {
      if (token === loadToken) ElMessage.error(pluginErrorMessage(err))
    } finally {
      if (token === loadToken) loading.value = false
    }
  },
  { immediate: true },
)

// 点复选框：先问后端，成功后才更新界面；失败时复选框自己弹回原状（因为我们用的是服务器返回的状态）
async function toggle(plugin, enabled) {
  busy.value = true
  try {
    const updated = await api.setEnabled(props.sessionId, plugin.name, enabled)
    plugins.value = replaceByName(plugins.value, updated)
    // 勾选工作流可能顺带勾上了同名插件（让插件框刷新）；后端有提醒（例如没有同名插件）就明确告诉用户
    emit('toggled')
    if (updated.notice) ElMessage({ type: 'warning', message: updated.notice, duration: 8000, showClose: true })
    else if (isWorkflow && enabled) ElMessage.success('已启用，下一条消息起模型将按该格式输出')
  } catch (err) {
    ElMessage.error(pluginErrorMessage(err))
  } finally {
    busy.value = false
  }
}

// el-upload 的 before-upload 返回 false：不走它自带的上传，由我们自己传，这样能复用检查和统一的错误提示
async function onPick(file) {
  const problem = checkSkillFile(file)
  if (problem) {
    ElMessage.warning(problem)
    return false
  }
  if (!props.sessionId) {
    ElMessage.warning('请先选择或新建一个会话')
    return false
  }
  busy.value = true
  try {
    // 插件先读文件开头检查 frontmatter：不合格就提示原因，不上传；工作流对内容没有要求
    const contentProblem = isWorkflow ? null : checkSkillContent(await readHead(file))
    if (contentProblem) {
      ElMessage({ type: 'warning', message: contentProblem, duration: 6000, showClose: true })
      return false
    }
    const created = await api.add(props.sessionId, file)
    plugins.value = [...plugins.value, created].sort((a, b) => a.name.localeCompare(b.name))
    ElMessage.success(`已添加${noun} ${created.name}`)
  } catch (err) {
    ElMessage.error(pluginErrorMessage(err))
  } finally {
    busy.value = false
  }
  return false
}

// 点名称：弹出二级对话框，只读显示这个文件的全文（不改内容、不影响勾选）
const viewing = ref(null) // { name, content } 或 null
const viewVisible = ref(false)
async function view(plugin) {
  try {
    viewing.value = await api.get(props.sessionId, plugin.name)
    viewVisible.value = true
  } catch (err) {
    ElMessage.error(pluginErrorMessage(err))
  }
}

async function removeChecked() {
  const targets = deletable.value
  if (!targets.length) return
  try {
    await ElMessageBox.confirm(
      `确定删除已勾选的 ${targets.length} 个${noun}？\n${targets.map((p) => p.name).join('、')}`,
      `删除${noun}`,
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 点了取消
  }
  busy.value = true
  const failed = []
  for (const plugin of targets) {
    try {
      await api.remove(props.sessionId, plugin.name)
      plugins.value = removeByName(plugins.value, plugin.name) // 删成功一个就从列表拿掉一个，中途失败也不会和后端对不上
    } catch (err) {
      failed.push(`${plugin.name}：${pluginErrorMessage(err)}`)
    }
  }
  busy.value = false
  if (failed.length) ElMessage.error(`有 ${failed.length} 个删除失败：${failed.join('；')}`)
  else ElMessage.success(`已删除 ${targets.length} 个${noun}`)
}
</script>

<template>
  <section class="plugins" :aria-label="noun">
    <div class="header">
      <h3 class="title">{{ isWorkflow ? '工作流(Workflow)' : '插件(Skills)' }}</h3>
    </div>

    <div v-loading="loading" class="list">
      <el-empty v-if="!loading && !plugins.length" :description="`这个会话还没有${noun}`" :image-size="60" />
      <el-checkbox
        v-for="p in plugins"
        :key="p.name"
        :model-value="p.enabled"
        :disabled="busy"
        class="item"
        @change="(value) => toggle(p, value)"
      >
        <!-- .prevent.stop：点名称只看内容，不触发复选框的勾选 -->
        <span class="name" role="button" title="点击查看内容" @click.prevent.stop="view(p)">{{ p.name }}</span>
        <el-tag v-if="p.default" size="small" type="info" class="tag" :title="`默认${noun}：所有会话都有，不能删除`">默认</el-tag>
      </el-checkbox>
    </div>

    <div class="buttons">
      <el-upload
        :show-file-list="false"
        :auto-upload="true"
        :before-upload="onPick"
        :disabled="busy || !sessionId"
        :multiple="false"
        accept=".md"
        class="add"
      >
        <el-button :disabled="busy || !sessionId">增加</el-button>
      </el-upload>
      <el-button type="danger" plain :disabled="busy || !deletable.length" @click="removeChecked">删除</el-button>
    </div>

    <el-dialog v-model="viewVisible" :title="viewing?.name" width="640px" append-to-body>
      <el-input :model-value="viewing?.content" type="textarea" readonly :rows="18" resize="none" />
    </el-dialog>
  </section>
</template>

<style scoped>
/* 插件框和工作流框上下叠放在 ChatView 的右栏里：各占一半高度；整栏的收起 / 展开由 ChatView 管 */
.plugins {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  padding: 16px;
  overflow: hidden;
}
.plugins + .plugins {
  border-top: 1px solid #e4e7ed;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}
/* 列表占满剩下的高度，插件多了就在列表里滚动；按钮始终固定在底部 */
.list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
/* 竖向排列：每个复选框独占一行 */
.item {
  display: flex;
  align-items: flex-start;
  height: auto;
  margin: 0 0 8px;
  white-space: normal;
}
.item :deep(.el-checkbox__label) {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.4;
}
.name {
  cursor: pointer;
  font-size: 14px;
  word-break: break-all;
}
.name:hover {
  color: #409eff;
  text-decoration: underline;
}
.tag {
  align-self: flex-start;
  margin-top: 2px;
}
.buttons {
  display: flex;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid #e4e7ed;
}
.buttons .add {
  display: inline-flex;
}
</style>
