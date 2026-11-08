<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { NButton, NInput, NSelect, NTabPane, NTabs } from 'naive-ui'
import { message } from '../utils/feedback'
import { createSession, listMessages, listSessions, openStream, streamMessage, uploadFile } from '../api'
import EurekaBanner from '../components/EurekaBanner.vue'
import FileChangeBanner from '../components/FileChangeBanner.vue'
import WaitingHint from '../components/WaitingHint.vue'
import SkillSelectionBanner from '../components/SkillSelectionBanner.vue'
import EurekaRunPanel from '../components/EurekaRunPanel.vue'
import FileUploadButton from '../components/FileUploadButton.vue'
import MarkdownPreview from '../components/MarkdownPreview.vue'
import ModelPicker from '../components/ModelPicker.vue'
import PreviewPane from '../components/PreviewPane.vue'
import WorkspaceBrowser from '../components/WorkspaceBrowser.vue'
import PluginPanel from '../components/PluginPanel.vue'
import SessionStats from '../components/SessionStats.vue'
import { loadLastSession, pickSession, saveLastSession } from '../utils/lastSession'
import { extractLinks } from '../utils/eurekaRun'
import { buildSendOptions } from '../utils/options'
import { sendWithAttachment } from '../utils/sendFlow'
import { createTypewriter } from '../utils/typewriter'
import { formatSize, uploadErrorMessage } from '../utils/upload'

const sessions = ref([])
const currentId = ref(null)
const messages = ref([])
// 最近一条带「选中提示」的消息：右栏据此让被选中的插件 / 工作流变色
const lastSelection = computed(() => [...messages.value].reverse().find((m) => m.skill_selection))
const draft = ref('')
const pendingFile = ref(null) // 已选好、还没上传的文件；点「发送」时才上传
const stage = ref('') // 发送过程中给用户看的一句话（比如「上传文件中…」）
const modelChoice = ref(null) // 选的模型和推理强度 { model, effort }；选项还没加载出来时是 null
const sending = ref(false)
const waitingId = ref(null) // 正在等回复的会话 id；等到第一段回复（或出错）就清空
// 没写文字、但上传了文件时，用这句话代替，让模型照着去做
const DEFAULT_TEXT = '请根据我上传的文件开始。'
const rightCollapsed = ref(false) // 右栏（插件 + 工作流）是否收起（宽屏用）
// 窄屏（手机）一次只显示一个页面，由左右各两个悬浮按钮切换；宽屏不用这个状态
const LEFT_FABS = [
  { view: 'sessions', icon: '☰', title: '会话列表' },
  { view: 'chat', icon: '💬', title: '对话' },
]
const RIGHT_FABS = [
  { view: 'plugins', icon: '🧩', title: '插件和工作流' },
]
const view = ref('chat') // sessions | chat | plugins（插件和工作流合在一页）
const tab = ref('chat') // 会话框上面的标签：chat（对话）| workspace（工作区）| preview（预览）
const previewLinks = ref([]) // 预览页要内嵌的链接：取编号最大（最新）那次工作流运行日志里的
let previewRunId = 0
const previewPane = ref(null)
const openTarget = ref(null) // 要在工作区打开的文件 { path }
const listEl = ref(null)

// 窄屏悬浮按钮：再点一次当前所在的会话列表 / 插件页，收起它回到对话页
function toggleView(target) {
  view.value = view.value === target && target !== 'chat' ? 'chat' : target
}

let closeStream = null

// 离底部不到这个距离，就认为用户正跟着看最新内容；超过了说明在往上翻，不能把页面拽回去
const STICK_TO_BOTTOM_PX = 80

function scrollToBottom({ force = false } = {}) {
  const el = listEl.value
  if (!el) return
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_TO_BOTTOM_PX
  if (force || nearBottom) nextTick(() => (el.scrollTop = el.scrollHeight))
}

// 发送的回复和 SSE 推送可能带来同一条消息，按 id 去重
function addMessages(incoming) {
  const known = new Set(messages.value.map((m) => m.id))
  for (const m of incoming) if (!known.has(m.id)) messages.value.push(m)
  messages.value.sort((a, b) => a.id - b.id)
  scrollToBottom()
}

async function selectSession(id) {
  if (closeStream) closeStream()
  currentId.value = id
  saveLastSession(id) // 刷新后回到这个会话
  pendingFile.value = null // 选好还没传的文件属于原来的会话，不能带到新会话里
  previewLinks.value = [] // 预览属于原来的会话
  previewRunId = 0
  messages.value = []
  addMessages(await listMessages(id))
  const last = messages.value.length ? messages.value[messages.value.length - 1].id : 0
  closeStream = openStream(id, last, (m) => addMessages([m]))
}

// 会话下拉框的选项
const sessionOptions = computed(() => sessions.value.map((s) => ({ label: s.title || '新会话', value: s.id })))

const currentSession = computed(() => sessions.value.find((s) => s.id === currentId.value))

// 每次请求完成后更新一次计数：重新取会话列表，只取当前会话的三个计数
async function refreshCounts() {
  const fresh = (await listSessions()).find((s) => s.id === currentId.value)
  const current = sessions.value.find((s) => s.id === currentId.value)
  if (fresh && current) Object.assign(current, fresh)
}

/** 工作流进度更新：把最新一次运行日志里的链接给预览页。 */
function onRunUpdate(run) {
  const links = extractLinks(run)
  if (run.id < previewRunId || !links.length) return
  previewRunId = run.id
  if (links.join() !== previewLinks.value.join()) previewLinks.value = links
}

/** 点了日志里的访问地址：在预览页新建一个页面并跳转过去显示。 */
async function openInPreview(link) {
  if (!previewLinks.value.includes(link)) previewLinks.value = [...previewLinks.value, link]
  tab.value = 'preview'
  await nextTick()
  previewPane.value?.open(link)
}

/** 工作流刚完成：有链接就跳到预览页。 */
function onRunFinished(run) {
  if (extractLinks(run).length) tab.value = 'preview'
}

/** 文件变更提示条里点了一个文件：切到工作区标签并打开它。 */
function openInWorkspace(path) {
  openTarget.value = { path }
  tab.value = 'workspace'
}

async function newSession() {
  const created = await createSession()
  sessions.value.unshift(created)
  await selectSession(created.id)
}

// 回车发送；Shift+回车走默认行为换行；输入法选字时的回车不发送
function onEnter(e) {
  if (e.isComposing) return
  e.preventDefault()
  send()
}

async function send() {
  const attachment = pendingFile.value
  const content = draft.value.trim() || (attachment ? DEFAULT_TEXT : '')
  const options = buildSendOptions(modelChoice.value) // 点发送这一刻的选择，随这句话一起透传给后端
  // 有文件时，没有文字也能发；什么都没有就不动
  if (!content || !currentId.value || sending.value) return
  sending.value = true
  try {
    // 先上传文件，await 它结束，才发送文本——sendWithAttachment 保证这个先后顺序，上传失败就不会发文本
    const { uploaded } = await sendWithAttachment({
      file: attachment,
      text: content,
      upload: (file) => {
        stage.value = '上传文件中…'
        return uploadFile(currentId.value, file)
      },
      send: (text) => {
        stage.value = ''
        return streamReply(text, options)
      },
    })
    if (uploaded) {
      const renamed = uploaded.name !== attachment.name ? `（重名，已存为 ${uploaded.name}）` : ''
      message.success(`已上传 ${uploaded.name}，${formatSize(uploaded.size)}${renamed}`)
    }
    // 成功才清空；失败时保留文字和文件，用户可以直接重试
    draft.value = ''
    pendingFile.value = null
    refreshCounts()
  } catch (e) {
    if (e.stage === 'upload') {
      message.error(`文件上传失败，文本未发送：${uploadErrorMessage(e.cause)}`)
    } else {
      // 发送阶段失败：文件其实已经传上去了，别让用户重传，只需重发文字
      if (attachment) pendingFile.value = null
      message.error(attachment ? '文件已上传，但消息发送失败，请重试' : '发送失败，请重试')
    }
  } finally {
    stage.value = ''
    sending.value = false
  }
}

// 发送文本并把回复一段段显示出来。出错时往外抛，由 send() 统一提示；这里负责清理半截的临时消息。
async function streamReply(content, options) {
  // 正在长出来的回复：先用一条临时消息（id 取 Infinity，排在最后），done 到来时换成存好的真消息。
  // live 必须是 messages.value 里取回的响应式代理：改 push 之前的原对象，Vue 看不到，界面不会刷新。
  const sessionId = currentId.value // 回复期间用户可能切走，标题要改在发送时的那个会话上
  waitingId.value = sessionId
  let live = null
  // delta 到达的节奏不均匀，交给打字机匀到每一帧，每帧最多更新一次界面
  const typewriter = createTypewriter((text) => {
    live.content += text
    scrollToBottom()
  })
  const dropLive = () => {
    waitingId.value = null
    typewriter.cancel()
    if (live) messages.value.splice(messages.value.indexOf(live), 1)
    live = null
  }
  try {
    await streamMessage(
      sessionId,
      content,
      {
        title: ({ title }) => {
          // 会话的第一句话：后端已把它改成标题，侧边栏同步显示
          const current = sessions.value.find((s) => s.id === sessionId)
          if (current) current.title = title
        },
        user: (m) => {
          addMessages([m])
          scrollToBottom({ force: true }) // 自己刚发的话，一定要看到
          nextTick(() => scrollToBottom({ force: true })) // 等待提示也要露出来
        },
        delta: ({ text }) => {
          if (!live) {
            waitingId.value = null
            messages.value.push({ id: Infinity, role: 'assistant', content: '' })
            live = messages.value[messages.value.length - 1]
          }
          typewriter.push(text)
        },
        done: (m) => {
          typewriter.finish() // 没打完的立刻补齐，再换成存好的真消息
          dropLive()
          addMessages([m])
        },
      },
      options, // { model, effort }：原样透传给后端
    )
  } catch (e) {
    dropLive() // 流中途断了：去掉半截的临时消息；已存的消息以服务器为准，刷新就能看到
    throw e
  }
}

onMounted(async () => {
  sessions.value = await listSessions()
  const id = pickSession(sessions.value, loadLastSession())
  if (id) await selectSession(id)
  else await newSession()
})
onBeforeUnmount(() => closeStream && closeStream())
</script>

<template>
  <div class="chat" :data-view="view">
    <aside class="sidebar">
      <n-button type="primary" class="new" @click="newSession">新建会话</n-button>
      <div
        v-for="s in sessions"
        :key="s.id"
        class="session"
        :class="{ active: s.id === currentId }"
        @click="selectSession(s.id); view = 'chat'"
      >
        {{ s.title }}
        <span class="time">{{ new Date(s.created_at).toLocaleString() }}</span>
      </div>
    </aside>

    <!-- 窄屏专用：左右各两个悬浮按钮，一次只显示一个页面（左：会话 / 对话；右：插件 / 工作流） -->
    <div class="fabs left">
      <n-button
        v-for="f in LEFT_FABS"
        :key="f.view"
        circle
        class="fab"
        :type="view === f.view ? 'primary' : 'default'"
        :title="f.title"
        @click="toggleView(f.view)"
      >
        {{ f.icon }}
      </n-button>
    </div>
    <div class="fabs right">
      <n-button
        v-for="f in RIGHT_FABS"
        :key="f.view"
        circle
        class="fab"
        :type="view === f.view ? 'primary' : 'default'"
        :title="f.title"
        @click="toggleView(f.view)"
      >
        {{ f.icon }}
      </n-button>
    </div>

    <main class="main">
      <!-- 标签行：选择会话、切换到工作区 -->
      <div class="tabs">
        <n-tabs v-model:value="tab" class="tab-switch">
          <n-tab-pane name="chat" display-directive="show"><template #tab>💬 会话</template></n-tab-pane>
          <n-tab-pane name="workspace" display-directive="show"><template #tab>📁 工作区</template></n-tab-pane>
          <n-tab-pane name="preview" display-directive="show"><template #tab>🌐 预览</template></n-tab-pane>
        </n-tabs>
        <n-select
          :value="currentId"
          size="small"
          class="session-select"
          placeholder="选择会话"
          :options="sessionOptions"
          @update:value="selectSession"
        />
      </div>

      <PreviewPane ref="previewPane" v-show="tab === 'preview'" :links="previewLinks" />
      <WorkspaceBrowser v-if="tab === 'workspace'" :session-id="currentId" :target="openTarget" />
      <div v-show="tab === 'chat'" ref="listEl" class="messages">
        <div v-if="!messages.length" class="empty">发送第一条消息，开始对话</div>
        <div v-for="m in messages" :key="m.id" class="row" :class="m.role">
          <div class="bubble">{{ m.content }}</div>
          <!-- eureka 时刻：挂在这条模型回复下面的提示条；不是消息内容，刷新后由后端带回来 -->
          <EurekaBanner v-if="m.eureka" :text="m.eureka.text" />
          <!-- 文件变更：这一轮工作区有文件创建或变更；和 eureka 提示条一样，刷新后由后端带回来 -->
          <!-- 选中 skill：skill_selector 这一轮选中了插件/工作流；和文件变更提示条一样，刷新后由后端带回来 -->
          <SkillSelectionBanner
            v-if="m.skill_selection"
            :text="m.skill_selection.text"
            :plugins="m.skill_selection.plugins"
            :workflows="m.skill_selection.workflows"
          />
          <FileChangeBanner v-if="m.file_change" :text="m.file_change.text" :files="m.file_change.files" @open-file="openInWorkspace" />
          <!-- 勾选了工作流但回复没按格式：说明原因（后端写的，刷新后还在） -->
          <div v-if="m.notice" class="notice" role="status">{{ m.notice }}</div>
          <!-- 提示条下面：这条 eureka 展开了工作流就显示执行进度（刷新后由后端带回 run 的 id 和状态） -->
          <EurekaRunPanel
            v-if="m.eureka?.run && currentId"
            :key="`${currentId}-${m.eureka.run.id}`"
            :session-id="currentId"
            :run-id="m.eureka.run.id"
            :initial-status="m.eureka.run.status"
            @update="onRunUpdate"
            @finished="onRunFinished"
            @open-link="openInPreview"
          />
        </div>
        <!-- 等模型回复：随机词 + ...，第一段回复到来后就被真正的回复替换 -->
        <div v-if="waitingId && waitingId === currentId" class="row assistant">
          <WaitingHint :key="waitingId" />
        </div>
      </div>

      <div v-show="tab === 'chat'" class="composer">
        <MarkdownPreview :text="draft" />
        <n-input
          v-model:value="draft"
          type="textarea"
          :rows="3"
          :resizable="false"
          placeholder="支持 Markdown，Enter 发送，Shift+Enter 换行"
          @keydown.enter.exact="onEnter"
        />
        <!-- 文本框下方一排：左边选文件；右边依次是模型、推理强度、发送，三者垂直居中对齐 -->
        <div class="actions">
          <FileUploadButton v-model="pendingFile" :disabled="sending" />
          <div class="right">
            <SessionStats
              v-if="currentSession"
              :session-id="currentSession.id"
              :label="`${currentSession.request_count} 次请求 · ${currentSession.turn_count} 轮 · ${currentSession.tool_count} 次工具`"
            />
            <ModelPicker v-model="modelChoice" :disabled="sending" />
            <n-button
              type="primary"
              :loading="sending"
              :disabled="!draft.trim() && !pendingFile"
              @click="send"
            >
              {{ stage || '发送' }}
            </n-button>
          </div>
        </div>
      </div>
    </main>

    <!-- 右栏：插件框在上，工作流框在下；一个按钮同时收起 / 展开两个框（只是隐藏，状态不丢） -->
    <aside class="side-panels" :class="{ collapsed: rightCollapsed }">
      <n-button
        circle
        size="small"
        class="toggle"
        :title="rightCollapsed ? '展开插件和工作流' : '收起插件和工作流'"
        @click="rightCollapsed = !rightCollapsed"
      >
        {{ rightCollapsed ? '‹' : '×' }}
      </n-button>
      <div v-show="!rightCollapsed" class="panels">
        <PluginPanel :session-id="currentId" :selected="lastSelection?.skill_selection.plugins" :selected-key="lastSelection?.id" />
        <PluginPanel
          :session-id="currentId"
          kind="workflow"
          :selected="lastSelection?.skill_selection.workflows"
          :selected-key="lastSelection?.id"
        />
      </div>
    </aside>
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  height: 100%;
}
.fabs {
  display: none; /* 只在窄屏显示 */
}
.side-panels {
  position: relative;
  width: 240px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid #e4e7ed;
  transition: width 0.2s;
}
.side-panels.collapsed {
  width: 44px;
}
.toggle {
  position: absolute;
  top: 12px;
  right: 8px;
  z-index: 1;
}
.collapsed .toggle {
  right: 6px;
}
.panels {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.sidebar {
  width: 240px;
  padding: 16px;
  box-sizing: border-box;
  border-right: 1px solid #e4e7ed;
  overflow-y: auto;
}
.new {
  width: 100%;
  margin-bottom: 16px;
}
.session {
  padding: 8px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
}
.session:hover {
  background: #f5f7fa;
}
.session.active {
  background: #ecf5ff;
  color: #409eff;
}
.time {
  display: block;
  font-size: 12px;
  color: #909399;
}
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.tabs {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 20px;
  border-bottom: 1px solid #e4e7ed;
}
/* 只用标签头切换，内容区由下面各自的 v-show 控制；tab-pane 本身没有内容，所以去掉它占的空间 */
.tab-switch :deep(.n-tabs-pane-wrapper),
.tab-switch :deep(.n-tab-pane) {
  display: none !important; /* 空的 pane 自带 12px 顶部内边距，会把标签顶偏，和右边的下拉框对不齐 */
}
.tab-switch :deep(.n-tabs-nav) {
  border-bottom: none;
}
.session-select {
  width: 200px;
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}
.empty {
  text-align: center;
  color: #909399;
  margin-top: 48px;
}
.notice {
  align-self: flex-start;
  max-width: 70%;
  margin-top: 6px;
  padding: 6px 12px;
  border-radius: 8px;
  background: #fdf6ec;
  color: #b88230;
  font-size: 13px;
  line-height: 1.5;
}
.row {
  display: flex;
  flex-direction: column; /* 气泡在上，eureka 提示条（如果有）在下 */
  align-items: flex-start;
  margin-bottom: 16px;
}
.row.user {
  align-items: flex-end;
}
.bubble {
  max-width: 70%;
  padding: 8px 16px;
  border-radius: 8px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  background: #f4f4f5;
  /* 文字一直在长：让浏览器把重排限制在这个气泡内，不用每次都重算整个消息列表 */
  contain: layout paint;
}
.row.user .bubble {
  background: #409eff;
  color: #fff;
}
.composer {
  container: composer / inline-size; /* 子组件按输入区自身的宽度压缩（中间栏宽度取决于两侧栏，不能看屏幕宽度） */
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 20px;
  border-top: 1px solid #e4e7ed;
}
/* 按钮一排，任何宽度都不折行：左边选文件；右边是统计、模型、强度、发送。整排垂直居中，所以高度不同的控件也上下对齐。
   放不下时不换行，而是由各组件按 @container composer 逐级压缩（见 ModelPicker / SessionStats / FileUploadButton） */
.composer .actions {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  padding-bottom: 14px; /* 滑块的刻度文字挂在这一排下方，留出位置不被截 */
  justify-content: space-between;
  gap: 8px 16px;
}
.composer .actions > :first-child {
  flex: 0 1 auto; /* 左边先压缩：选了文件时文件名省略，右边的控件保持完整 */
  min-width: 34px; /* 至少留下「+」按钮，不能被压到 0 */
}
/* 手机档：选好文件后「+」和「✕」两个方块都要完整保留，空间由右边的模型 / 滑块让出 */
@container composer (max-width: 420px) {
  .composer .actions > :first-child {
    flex-shrink: 0;
  }
}
.composer .actions .right {
  display: flex;
  flex-wrap: nowrap;
  flex-shrink: 0;
  align-items: center;
  gap: 16px;
  margin-left: auto;
}
.composer .actions .right :deep(.n-button) {
  flex-shrink: 0;
}
@container composer (max-width: 600px) {
  .composer .actions,
  .composer .actions .right {
    gap: 8px;
  }
}
/* 窄屏（手机 390px）：三栏改为上下堆叠，聊天区占剩余高度，不再被两侧栏挤没 */
@media (max-width: 768px) {
  /* 一次只显示一个页面（由 data-view 决定），左右各两个悬浮按钮切换 */
  .chat {
    position: relative;
  }
  .fabs {
    position: absolute;
    top: 8px;
    z-index: 30;
    display: flex;
    gap: 8px;
  }
  .fabs.left {
    left: 8px;
  }
  .fabs.right {
    right: 8px;
  }
  .fab {
    width: 44px;
    height: 44px;
    margin: 0;
    font-size: 20px;
  }
  .sidebar,
  .main,
  .side-panels {
    display: none;
    flex: 1;
    width: auto;
    min-height: 0;
    border: none;
    margin-top: 64px; /* 给悬浮按钮让位；用 margin 而不是 padding，滚动条才从按钮下面开始 */
    box-sizing: border-box;
    overflow-y: auto;
  }
  .chat[data-view='sessions'] .sidebar,
  .chat[data-view='chat'] .main,
  .chat[data-view='plugins'] .side-panels {
    display: flex;
    flex-direction: column;
  }
  .toggle {
    display: none; /* 窄屏不用右栏的收起按钮 */
  }
  .panels,
  .collapsed .panels {
    display: flex !important; /* 窄屏右栏始终展开 */
  }
  .bubble,
  .notice {
    max-width: 90%;
  }
  /* 标签行：标签可横向滚动，会话下拉缩小；两侧内边距减小 */
  .tabs {
    padding: 0 8px;
    gap: 8px;
  }
  .tab-switch {
    flex: 1;
    min-width: 0;
  }
  .session-select {
    width: 104px;
    flex-shrink: 0;
  }
  /* 标签之间的间距（默认 36px，是组件内联的变量，覆盖不了）收小：改垫片宽度，360px 宽的手机上三个标签才放得下 */
  .tab-switch :deep(.n-tabs-tab-pad) {
    width: 14px;
  }
  .messages {
    padding: 12px;
  }
  /* 输入区贴底时避开 iPhone 底部横条 */
  .composer {
    padding: 8px 12px calc(8px + env(safe-area-inset-bottom));
  }
}
</style>
