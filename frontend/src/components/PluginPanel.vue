<script setup>
// 页面最右侧的插件框（也用来做它下方的工作流框：kind="workflow"，逻辑相同，只是文案和接口不同，且上传不检查 frontmatter）：竖向列表，每一行是一个插件（skill）；下方一个按钮：增加。
// 插件属于「当前会话」：后端把它们存在这个会话自己的目录里，切换会话就换一份列表。
//   没有勾选：用不用、用哪个插件，由后端 core 的 skill_selector 在每句话之前选，这里只管文件的增删查。
//   增加：选一个 .md 文件上传，文件名就是插件名。文件开头必须有 frontmatter（name + description）：
//   选择器要读它。选好后先在浏览器里检查，不合格就提示，不会白传。
//   查看：点名称弹窗看全文。
//   删除：每行右边的「删除」（先二次确认）。默认插件（后端 config.ini 配置的目录里的）所有会话都有、不能删除，旁边带「默认」标记。
import { ref, watch } from 'vue'
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
} from '../api'
import {
  checkSkillContent,
  checkSkillFile,
  pluginErrorMessage,
  readHead,
  removeByName,
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
  ? { list: listWorkflows, get: getWorkflow, add: addWorkflow, remove: deleteWorkflow }
  : { list: listPlugins, get: getPlugin, add: addPlugin, remove: deletePlugin }

const plugins = ref([])
const loading = ref(false)
const busy = ref(false) // 增加 / 删除进行中，期间按钮都不可点，免得并发操作

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

async function remove(plugin) {
  try {
    await ElMessageBox.confirm(`确定删除${noun} ${plugin.name}？`, `删除${noun}`, {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 点了取消
  }
  busy.value = true
  try {
    await api.remove(props.sessionId, plugin.name)
    plugins.value = removeByName(plugins.value, plugin.name)
    ElMessage.success(`已删除${noun} ${plugin.name}`)
  } catch (err) {
    ElMessage.error(pluginErrorMessage(err))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="plugins" :aria-label="noun">
    <div class="header">
      <h3 class="title">{{ isWorkflow ? '工作流(Workflow)' : '插件(Skills)' }}</h3>
    </div>

    <div v-loading="loading" class="list">
      <el-empty v-if="!loading && !plugins.length" :description="`这个会话还没有${noun}`" :image-size="60" />
      <div v-for="p in plugins" :key="p.name" class="item">
        <span class="name" role="button" title="点击查看内容" @click="view(p)">{{ p.name }}</span>
        <el-tag v-if="p.default" size="small" type="info" class="tag" :title="`默认${noun}：所有会话都有，不能删除`">默认</el-tag>
        <el-button v-else link type="danger" size="small" class="del" :disabled="busy" @click="remove(p)">删除</el-button>
      </div>
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
/* 竖向排列：每个插件独占一行，删除按钮在右边 */
.item {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 8px;
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
  flex: none;
}
.del {
  flex: none;
  margin-left: auto;
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
