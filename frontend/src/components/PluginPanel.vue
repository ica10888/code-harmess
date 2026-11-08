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
  createPlugin,
  createWorkflow,
  updatePlugin,
  updateWorkflow,
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
  // 最近一次被选中的名字，和对应消息的 id（id 变了才重新变色）
  selected: { type: Array, default: () => [] },
  selectedKey: { type: [Number, String], default: null },
})

const isWorkflow = props.kind === 'workflow'
const noun = isWorkflow ? '工作流' : '插件'
const api = isWorkflow
  ? { list: listWorkflows, get: getWorkflow, add: addWorkflow, remove: deleteWorkflow, create: createWorkflow, update: updateWorkflow }
  : { list: listPlugins, get: getPlugin, add: addPlugin, remove: deletePlugin, create: createPlugin, update: updatePlugin }

// 选中的名字彩虹变色，5 秒后恢复：动画本身是 5 秒（见样式），这里只负责到点移除 class。
// selected 里是 skill 的 frontmatter name，和列表里的文件名不同，所以按 p.skill_name 比
const FLASH_MS = 5000
const flashing = ref([])
let flashTimer = null
watch(
  () => props.selectedKey,
  (key) => {
    clearTimeout(flashTimer)
    flashing.value = []
    if (key === null || key === Infinity || !props.selected.length) return
    // 先清空再下一帧设置，连续两次选中同一个名字时动画也能重新开始
    requestAnimationFrame(() => {
      flashing.value = [...props.selected]
      flashTimer = setTimeout(() => (flashing.value = []), FLASH_MS)
    })
  },
)

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

// 点名称：弹出二级对话框显示全文；默认项只读，自己的可以改后保存（覆盖原文件）
const viewing = ref(null) // { name, default, content } 或 null
const viewVisible = ref(false)
const draft = ref('') // 编辑框里的内容
async function view(plugin) {
  try {
    viewing.value = await api.get(props.sessionId, plugin.name)
    draft.value = viewing.value.content
    viewVisible.value = true
  } catch (err) {
    ElMessage.error(pluginErrorMessage(err))
  }
}
async function save() {
  busy.value = true
  try {
    viewing.value = await api.update(props.sessionId, viewing.value.name, draft.value)
    ElMessage.success(`已保存${noun} ${viewing.value.name}`)
    viewVisible.value = false
  } catch (err) {
    ElMessage({ type: 'error', message: pluginErrorMessage(err), duration: 6000, showClose: true })
  } finally {
    busy.value = false
  }
}

// 新建：填文件名和内容，直接提交（不用先准备文件）
const creating = ref(false)
const newName = ref('')
const newContent = ref('')
const SKILL_TEMPLATE = ['---', 'name: my-skill', 'description: 这个插件是做什么的、什么时候用', '---', ''].join('\n')
const WORKFLOW_TEMPLATE = ['---', 'name: my-flow', 'description: 这个工作流的输出格式', 'schema: {"type": "object"}', '---', ''].join('\n')
function openCreate() {
  newName.value = ''
  newContent.value = isWorkflow ? WORKFLOW_TEMPLATE : SKILL_TEMPLATE
  creating.value = true
}
async function create() {
  const name = newName.value.trim().replace(/(\.md)?$/i, '.md')
  busy.value = true
  try {
    const created = await api.create(props.sessionId, name, newContent.value)
    plugins.value = [...plugins.value, created].sort((a, b) => a.name.localeCompare(b.name))
    ElMessage.success(`已新建${noun} ${created.name}`)
    creating.value = false
  } catch (err) {
    ElMessage({ type: 'error', message: pluginErrorMessage(err), duration: 6000, showClose: true })
  } finally {
    busy.value = false
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
        <span class="name" :class="{ rainbow: flashing.includes(p.skill_name) }" role="button" title="点击查看内容" @click="view(p)">{{ p.name }}</span>
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
        <el-button :disabled="busy || !sessionId">上传</el-button>
      </el-upload>
      <el-button :disabled="busy || !sessionId" @click="openCreate">新建</el-button>
    </div>

    <el-dialog v-model="viewVisible" :title="viewing?.name" width="640px" append-to-body>
      <el-input v-model="draft" type="textarea" :readonly="viewing?.default" :rows="18" resize="none" />
      <template v-if="!viewing?.default" #footer>
        <el-button @click="viewVisible = false">取消</el-button>
        <el-button type="primary" :disabled="busy || draft === viewing?.content" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="creating" :title="`新建${noun}`" width="640px" append-to-body>
      <el-input v-model="newName" placeholder="文件名，如 notes.md" class="new-name" />
      <el-input v-model="newContent" type="textarea" :rows="14" resize="none" />
      <template #footer>
        <el-button @click="creating = false">取消</el-button>
        <el-button type="primary" :disabled="busy || !newName.trim()" @click="create">新建</el-button>
      </template>
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
  color: #303133;
  cursor: pointer;
  font-size: 14px;
  word-break: break-all;
}
.name:hover {
  color: #409eff;
  text-decoration: underline;
}
.name.rainbow {
  font-weight: 700;
  animation:
    rainbow-fade 5s linear forwards,
    rainbow-blink 0.5s ease-in-out 6;
}
/* 彩虹色轮流变（高饱和），越到后面越浅（向正常的深灰色混合），最后回到正常颜色 */
@keyframes rainbow-fade {
  0% { color: color-mix(in srgb, #ff0000 100%, #303133); }
  10% { color: color-mix(in srgb, #ff8800 100%, #303133); }
  20% { color: color-mix(in srgb, #ffe600 100%, #303133); }
  30% { color: color-mix(in srgb, #00e000 95%, #303133); }
  40% { color: color-mix(in srgb, #00e5ff 85%, #303133); }
  50% { color: color-mix(in srgb, #0066ff 70%, #303133); }
  60% { color: color-mix(in srgb, #aa00ff 55%, #303133); }
  70% { color: color-mix(in srgb, #ff0000 40%, #303133); }
  80% { color: color-mix(in srgb, #ff8800 28%, #303133); }
  90% { color: color-mix(in srgb, #00e000 14%, #303133); }
  100% { color: #303133; font-weight: 400; }
}
/* 闪动：前 3 秒每 0.5 秒一次明暗，之后保持不透明，只剩颜色渐淡 */
@keyframes rainbow-blink {
  0%, 100% { opacity: 1; text-shadow: 0 0 6px currentColor; }
  50% { opacity: 0.25; text-shadow: none; }
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
.new-name {
  margin-bottom: 8px;
}
.buttons .add {
  display: inline-flex;
}
</style>
