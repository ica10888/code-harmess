<script setup>
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { NButton, NIcon, NInput, NTabPane, NTabs } from 'naive-ui'
import {
  AddCircleOutline,
  ChatbubbleEllipsesOutline,
  ChatbubblesOutline,
  ChatbubbleOutline,
  ChevronForward,
  ExtensionPuzzleOutline,
  FolderOpenOutline,
  GlobeOutline,
  MenuOutline,
  PaperPlaneOutline,
} from '@vicons/ionicons5'
import { message } from '../utils/feedback'
import { createSession, listMessages, listSessions, openStream, streamMessage, uploadFile } from '../api'
import EurekaBanner from '../components/EurekaBanner.vue'
import FileChangeBanner from '../components/FileChangeBanner.vue'
import WaitingHint from '../components/WaitingHint.vue'
import SkillSelectionBanner from '../components/SkillSelectionBanner.vue'
import ModelPicker from '../components/ModelPicker.vue'
import ColourfulText from '../components/ColourfulText.vue'
import MeteorShower from '../components/MeteorShower.vue'
// 非首屏必需的组件异步加载：工作区（含代码编辑器）、预览、会话统计、进度面板、插件面板、输入预览、上传按钮
const loadEurekaRunPanel = () => import('../components/EurekaRunPanel.vue')
const EurekaRunPanel = defineAsyncComponent(loadEurekaRunPanel)
const loadPreviewPane = () => import('../components/PreviewPane.vue')
const PreviewPane = defineAsyncComponent(loadPreviewPane)
const loadWorkspaceBrowser = () => import('../components/WorkspaceBrowser.vue')
const WorkspaceBrowser = defineAsyncComponent(loadWorkspaceBrowser)
const loadPluginPanel = () => import('../components/PluginPanel.vue')
const PluginPanel = defineAsyncComponent(loadPluginPanel)
const loadMarkdownPreview = () => import('../components/MarkdownPreview.vue')
const MarkdownPreview = defineAsyncComponent(loadMarkdownPreview)
const loadFileUploadButton = () => import('../components/FileUploadButton.vue')
const FileUploadButton = defineAsyncComponent(loadFileUploadButton)
const loadSessionStats = () => import('../components/SessionStats.vue')
const SessionStats = defineAsyncComponent(loadSessionStats)
import { prefetchWhenIdle } from '../utils/prefetch'
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
const finished = reactive(new Set()) // 刚流式输出完的回复 id：气泡保留「完成态」的光边（刷新页面后不再有）
// 每个会话「一轮结束」的次数：传给统计弹窗，变了就重取平均值和明细（计数文字在轮结束时不变，靠它触发）
const statsVersion = reactive({})
const bumpStats = (id) => (statsVersion[id] = (statsVersion[id] || 0) + 1)
const pendingSelection = ref(null) // 刚选中 skill 还没存库的提示 {sessionId, ...}；回复存好后由消息自带的 skill_selection 接管
// 当前会话里该显示的临时提示条（别的会话的不显示）；有正在长的回复时画在那条回复里，没有就单独画一行（见模板）
const shownPending = computed(() => (pendingSelection.value?.sessionId === currentId.value ? pendingSelection.value : null))
const hasLive = computed(() => messages.value.some((m) => m.id === Infinity))
const waitingId = ref(null) // 正在等回复的会话 id；等到第一段回复（或出错）就清空
// 没写文字、但上传了文件时，用这句话代替，让模型照着去做
const DEFAULT_TEXT = '请根据我上传的文件开始。'
const rightCollapsed = ref(false) // 右栏（插件 + 工作流）是否收起（宽屏用）
// 窄屏（手机）一次只显示一个页面，由左右各两个悬浮按钮切换；宽屏不用这个状态
const LEFT_FABS = [
  { view: 'sessions', icon: MenuOutline, title: '会话列表' },
  { view: 'chat', icon: ChatbubbleEllipsesOutline, title: '对话' },
]
const RIGHT_FABS = [
  { view: 'plugins', icon: ExtensionPuzzleOutline, title: '插件和工作流' },
]
// 侧栏标题的配色：黑色打底，夹杂冷色（钢蓝、青灰）
const BRAND_COLORS = ['#0b0f14', '#1c2733', '#2f4a63', '#3d6b8c', '#4f7f99', '#111820']
const view = ref('chat') // sessions | chat | plugins（插件和工作流合在一页）
const tab = ref('chat') // 会话框上面的标签：chat（对话）| workspace（工作区）| preview（预览）
// 已经展示过的消息 id：它们的提示框不再等间隔。切到别的页面时，会话区被隐藏，回来时浏览器会把 CSS 动画重播一遍，
// 所以离开会话页那一刻，把当时所有消息标成「已展示」，回来就没有间隔了（见下面的 watch）
const gapDone = reactive(new Set())
// 离开会话页（切到工作区 / 预览，或手机上切到别的页面）：当前消息都算展示过
watch([tab, view], ([t, v]) => {
  if (t !== 'chat' || v !== 'chat') messages.value.forEach((m) => gapDone.add(m.id))
})
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

// 回复下面的三个提示框（eureka、文件变更、工作流）不一起蹦出来，而是按顺序一个接一个
// （选用插件的提示条在回复上面，回复一开始就在，不参与排序）：
// 每个框的入场动画延迟 = 它前面「实际出现的框」数量 × BANNER_GAP_S。这里只算出 --gap，动画写在各组件里。
const BANNER_GAP_S = 0.6
function gapSeconds(m, slot) {
  if (gapDone.has(m.id)) return 0
  const shown = [!!m.eureka, !!m.file_change, !!m.eureka?.run]
  const before = shown.slice(0, slot).filter(Boolean).length
  return (before + 1) * BANNER_GAP_S // +1：第一个框也等一会儿，让回复文字先落定
}
const gapStyle = (m, slot) => ({ '--gap': `${gapSeconds(m, slot)}s` })

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
  if (incoming.some((m) => m.role === 'assistant' && m.skill_selection)) pendingSelection.value = null
  messages.value.sort((a, b) => a.id - b.id)
  scrollToBottom()
}

async function selectSession(id) {
  if (closeStream) closeStream()
  stopPolling()
  currentId.value = id
  saveLastSession(id) // 刷新后回到这个会话
  pendingFile.value = null // 选好还没传的文件属于原来的会话，不能带到新会话里
  previewLinks.value = [] // 预览属于原来的会话
  previewRunId = 0
  messages.value = []
  finished.clear()
  gapDone.clear()
  pendingSelection.value = null
  await refreshCounts(id).catch(() => {}) // 计数可能在别处变过（切走时回复才完成）
  // 这一轮选完 skill 但回复还没存好：后端把提示存在会话上，刷新 / 切页回来也要看到；回复存好后由消息自带的提示条接替
  const session = sessions.value.find((s) => s.id === id)
  applyPending(id, session)
  addMessages(await listMessages(id))
  const last = messages.value.length ? messages.value[messages.value.length - 1].id : 0
  closeStream = openStream(id, last, (m) => addMessages([m]))
  if (session?.running && waitingId.value !== id) pollWhileRunning(id) // 这一轮还在后端跑，页面没有它的流：轮询跟进
}

// 会话下拉框的选项

const currentSession = computed(() => sessions.value.find((s) => s.id === currentId.value))

// 一轮还在后端跑、而这个页面没有它的流（刷新了，或切走又切回来）：每隔几秒取一次，计数和选用插件提示跟着库走，
// 直到这一轮结束。当前页面自己正在收流的那一轮（waitingId / sending）由流事件更新，不用轮询。
const POLL_RUNNING_MS = 2000
let pollTimer = null
function stopPolling() {
  clearTimeout(pollTimer)
  pollTimer = null
}
function pollWhileRunning(id) {
  stopPolling()
  pollTimer = setTimeout(async () => {
    if (currentId.value !== id) return // 切走了：这个会话不再跟
    if (sending.value) return pollWhileRunning(id) // 自己正在收流，等它结束再说
    await refreshCounts(id).catch(() => {})
    if (currentId.value !== id) return
    const current = sessions.value.find((s) => s.id === id)
    if (current?.running) {
      applyPending(id, current)
      pollWhileRunning(id)
    } else {
      // 这一轮结束了：回复已存好，把它和提示条补进来（SSE 的补发也会来，按 id 去重）
      bumpStats(id)
      pendingSelection.value = null
      addMessages(await listMessages(id, messages.value.filter((m) => Number.isFinite(m.id)).at(-1)?.id ?? 0))
    }
  }, POLL_RUNNING_MS)
}
// 会话上带着还没存好的选用插件提示，就显示成临时提示条；没有就清掉
function applyPending(id, session) {
  pendingSelection.value = session?.pending_selection ? { sessionId: id, ...session.pending_selection } : null
}

// 更新指定会话的计数：重新取会话列表。必须按「发送时的会话」而不是当前会话，回复期间用户可能已经切走
async function refreshCounts(id) {
  const fresh = (await listSessions()).find((s) => s.id === id)
  const current = sessions.value.find((s) => s.id === id)
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
  const sentId = currentId.value
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
  } catch (e) {
    if (e.stage === 'upload') {
      message.error(`文件上传失败，文本未发送：${uploadErrorMessage(e.cause)}`)
    } else {
      // 发送阶段失败：文件其实已经传上去了，别让用户重传，只需重发文字
      if (attachment) pendingFile.value = null
      message.error(attachment ? '文件已上传，但消息发送失败，请重试' : '发送失败，请重试')
    }
  } finally {
    refreshCounts(sentId).then(() => bumpStats(sentId)).catch(() => {}) // 失败也刷新：后端可能已经算过这一轮了
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
  let final = null // done 事件带来的、存好的真消息
  // 回复期间用户可能切到了别的会话：这时流事件只更新「发送时那个会话」的数据（标题、计数），
  // 不能碰当前显示的消息列表和提示条，否则 A 的回复会串到 B 里；A 的消息切回去时由接口取回
  const here = () => currentId.value === sessionId
  // delta 到达的节奏不均匀，交给打字机匀到每一帧，每帧最多更新一次界面
  const typewriter = createTypewriter((text) => {
    live.content += text
    scrollToBottom()
  })
  const dropLive = () => {
    waitingId.value = null
    typewriter.cancel()
    const at = live ? messages.value.indexOf(live) : -1
    if (at >= 0) messages.value.splice(at, 1)
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
          if (!here()) return
          addMessages([m])
          scrollToBottom({ force: true }) // 自己刚发的话，一定要看到
          nextTick(() => scrollToBottom({ force: true })) // 等待提示也要露出来
        },
        // skill_selector 一选完（正式请求之前）就推来：马上显示提示条，不等整轮结束
        skills: (selection) => {
          if (here()) pendingSelection.value = { sessionId, ...selection }
        },
        // 每次请求、每次工具调用、每轮结束，后端都推一次最新计数：标题边跑边涨，不用等整轮结束
        stats: ({ running, ...counts }) => {
          const current = sessions.value.find((s) => s.id === sessionId)
          if (current) Object.assign(current, counts, { running })
          if (!running) bumpStats(sessionId) // 轮结束：平均值和明细都变了
        },
        delta: ({ text }) => {
          if (!here()) return // 切走了：文字不显示，回来时从库里取完整回复
          if (!live) {
            waitingId.value = null
            messages.value.push({ id: Infinity, role: 'assistant', content: '' })
            live = messages.value[messages.value.length - 1]
          }
          typewriter.push(text)
        },
        // 网络已经收完，但屏幕上的文字还在按节奏慢慢长：先记下真消息，等显示完再换
        done: (m) => (final = here() ? m : null),
      },
      options, // { model, effort }：原样透传给后端
    )
    await typewriter.drain() // 让用户把已收到的文字看完（切会话 / 出错会 cancel，等待随之结束）
    if (!here()) dropLive() // 切走了：清掉这次的临时消息（切会话时列表已换，splice 找不到就跳过）
    else if (final && live) {
      finished.add(final.id) // 记下：这条回复刚长完，带着「完成态」效果留在列表里
      typewriter.finish()
      dropLive()
      pendingSelection.value = null
      addMessages([final])
    }
  } catch (e) {
    if (here()) pendingSelection.value = null
    dropLive() // 流中途断了：去掉半截的临时消息；已存的消息以服务器为准，刷新就能看到
    throw e
  }
}

onMounted(async () => {
  // 首屏之后，空闲时预取用户很可能点开的页面
  prefetchWhenIdle([loadWorkspaceBrowser, loadPreviewPane, loadSessionStats, loadEurekaRunPanel])
  sessions.value = await listSessions()
  const id = pickSession(sessions.value, loadLastSession())
  if (id) await selectSession(id)
  else await newSession()
})
onBeforeUnmount(() => {
  stopPolling()
  if (closeStream) closeStream()
})
</script>

<template>
  <div class="chat" :data-view="view">
    <aside class="sidebar">
      <div class="brand"><ColourfulText text="Code Harness" :colors="BRAND_COLORS" start-color="#8a96a3" /></div>
      <n-button type="primary" class="new" @click="newSession">
        <template #icon><n-icon class="new-icon"><AddCircleOutline /></n-icon></template>
        新建会话
      </n-button>
      <!-- 会话列表：逐项淡入（延迟随序号递增，最多错开 10 项，列表再长也不会等太久，借鉴 Inspira UI 的 Animated List） -->
      <div
        v-for="(s, i) in sessions"
        :key="s.id"
        class="session"
        :class="{ active: s.id === currentId }"
        :style="{ animationDelay: `${Math.min(i, 10) * 40}ms` }"
        @click="selectSession(s.id); view = 'chat'"
      >
        <n-icon class="session-icon" :size="18"><component :is="s.id === currentId ? ChatbubbleEllipsesOutline : ChatbubbleOutline" /></n-icon>
        <span class="session-text">
          {{ s.title }}
          <span class="time">{{ new Date(s.created_at).toLocaleString() }}</span>
        </span>
      </div>
    </aside>

    <!-- 窄屏专用：左右各两个悬浮按钮，一次只显示一个页面（左：会话 / 对话；右：插件 / 工作流） -->
    <div class="fabs left">
      <n-button
        v-for="f in LEFT_FABS"
        :key="f.view"
        circle
        class="fab"
        :class="{ on: view === f.view }"
        :type="view === f.view ? 'primary' : 'default'"
        :title="f.title"
        @click="toggleView(f.view)"
      >
        <n-icon class="fab-icon" :size="22"><component :is="f.icon" /></n-icon>
      </n-button>
    </div>
    <div class="fabs right">
      <n-button
        v-for="f in RIGHT_FABS"
        :key="f.view"
        circle
        class="fab"
        :class="{ on: view === f.view }"
        :type="view === f.view ? 'primary' : 'default'"
        :title="f.title"
        @click="toggleView(f.view)"
      >
        <n-icon class="fab-icon" :size="22"><component :is="f.icon" /></n-icon>
      </n-button>
    </div>

    <main class="main">
      <!-- 标签行：切换 会话 / 工作区 / 预览 -->
      <div class="tabs">
        <n-tabs v-model:value="tab" class="tab-switch">
          <n-tab-pane name="chat" display-directive="show"><template #tab><span class="tab-label"><n-icon><ChatbubbleEllipsesOutline /></n-icon>会话</span></template></n-tab-pane>
          <n-tab-pane name="workspace" display-directive="show"><template #tab><span class="tab-label"><n-icon><FolderOpenOutline /></n-icon>工作区</span></template></n-tab-pane>
          <n-tab-pane name="preview" display-directive="show"><template #tab><span class="tab-label"><n-icon><GlobeOutline /></n-icon>预览</span></template></n-tab-pane>
        </n-tabs>
      </div>

      <PreviewPane ref="previewPane" v-show="tab === 'preview'" :links="previewLinks" />
      <WorkspaceBrowser v-if="tab === 'workspace'" :session-id="currentId" :target="openTarget" />
      <!-- 消息区外面包一层：流星雨铺在背景（不随消息滚动），消息列表在它上面滚动 -->
      <div v-show="tab === 'chat'" class="messages-wrap">
        <MeteorShower />
      <div ref="listEl" class="messages">
        <div v-if="!messages.length" class="empty">
          <n-icon class="empty-icon" :size="56"><ChatbubblesOutline /></n-icon>
          <div>发送第一条消息，开始对话</div>
        </div>
        <div v-for="m in messages" :key="m.id" class="row" :class="[m.role, { live: m.id === Infinity, settled: finished.has(m.id) }]">
          <!-- 选中 skill：skill_selector 在回复之前触发，所以提示条放在回复气泡的上面（和边跑边推的临时条同一个位置）；刷新后由后端带回来 -->
          <SkillSelectionBanner
            v-if="m.skill_selection"
            :text="m.skill_selection.text"
            :plugins="m.skill_selection.plugins"
            :workflows="m.skill_selection.workflows"
            :style="{ '--gap': '0s' }"
          />
          <!-- 正在长的回复：存库前的临时提示条也画在它上面，不能留在列表外面被挤到它下面 -->
          <SkillSelectionBanner
            v-if="m.id === Infinity && shownPending"
            :text="shownPending.text"
            :plugins="shownPending.plugins"
            :workflows="shownPending.workflows"
            :style="{ '--gap': '0s' }"
          />
          <div class="bubble">{{ m.content }}</div>
          <!-- eureka 时刻：挂在这条模型回复下面的提示条；不是消息内容，刷新后由后端带回来 -->
          <EurekaBanner v-if="m.eureka" :text="m.eureka.text" :style="gapStyle(m, 0)" />
          <!-- 文件变更：这一轮工作区有文件创建或变更；和 eureka 提示条一样，刷新后由后端带回来 -->
          <FileChangeBanner v-if="m.file_change" :text="m.file_change.text" :files="m.file_change.files" :style="gapStyle(m, 1)" @open-file="openInWorkspace" />
          <!-- 勾选了工作流但回复没按格式：说明原因（后端写的，刷新后还在） -->
          <div v-if="m.notice" class="notice" role="status">{{ m.notice }}</div>
          <!-- 提示条下面：这条 eureka 展开了工作流就显示执行进度（刷新后由后端带回 run 的 id 和状态） -->
          <EurekaRunPanel
            v-if="m.eureka?.run && currentId"
            :key="`${currentId}-${m.eureka.run.id}`"
            :session-id="currentId"
            :run-id="m.eureka.run.id"
            :initial-status="m.eureka.run.status"
            :style="gapStyle(m, 2)"
            :gap="gapSeconds(m, 2)"
            @update="onRunUpdate"
            @finished="onRunFinished"
            @open-link="openInPreview"
          />
        </div>
        <!-- 刚选中 skill：回复还没存库，先单独显示提示条；回复存好后由那条消息自己的提示条接替 -->
        <div v-if="shownPending && !hasLive" class="row assistant">
          <SkillSelectionBanner
            :text="shownPending.text"
            :plugins="shownPending.plugins"
            :workflows="shownPending.workflows"
          />
        </div>
        <!-- 等模型回复：随机词 + ...，第一段回复到来后就被真正的回复替换 -->
        <div v-if="waitingId && waitingId === currentId" class="row assistant">
          <WaitingHint :key="waitingId" />
        </div>
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
              :version="statsVersion[currentSession.id] || 0"
            />
            <ModelPicker v-model="modelChoice" :disabled="sending" />
            <n-button
              type="primary"
              class="send"
              :loading="sending"
              :disabled="!draft.trim() && !pendingFile"
              @click="send"
            >
              <template #icon><n-icon class="send-icon"><PaperPlaneOutline /></n-icon></template>
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
        <n-icon class="toggle-icon" :class="{ flip: rightCollapsed }"><ChevronForward /></n-icon>
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
.brand {
  margin-bottom: 12px;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.5px;
}
.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
/* 图标悬停：放大、轻微旋转并发光；尊重「减少动态效果」设置 */
.fab-icon,
.tab-label .n-icon {
  transition: transform 0.25s ease, filter 0.25s ease;
}
.fab:hover .fab-icon,
.tab-label:hover .n-icon {
  transform: scale(1.25) rotate(-8deg);
  filter: drop-shadow(0 0 6px #3d6b8c);
}
/* 收起按钮：箭头随收起 / 展开翻转；新建按钮的图标悬停时转一圈 */
.toggle-icon,
.new-icon {
  transition: transform 0.35s ease, filter 0.25s ease;
}
.toggle-icon.flip {
  transform: rotate(180deg);
}
.toggle:hover .toggle-icon {
  transform: translateX(3px) scale(1.2);
  filter: drop-shadow(0 0 5px #3d6b8c);
}
.toggle:hover .toggle-icon.flip {
  transform: rotate(180deg) translateX(3px) scale(1.2);
}
.toggle:active .toggle-icon {
  transform: scale(0.8);
}
.toggle:active .toggle-icon.flip {
  transform: rotate(180deg) scale(0.8);
}
.new:hover .new-icon {
  transform: rotate(90deg) scale(1.25);
  filter: drop-shadow(0 0 5px #ffffff);
}
.new:active .new-icon {
  transform: rotate(180deg) scale(0.8);
}
/* 发送按钮：悬停时纸飞机向右上飞出一点，按下时飞得更远并缩小；可发送时图标轻轻浮动，提示「可以点了」 */
.send-icon {
  transition: transform 0.25s ease;
}
.send:not(:disabled):hover .send-icon {
  transform: translate(3px, -3px) rotate(-12deg) scale(1.15);
}
.send:not(:disabled):active .send-icon {
  transform: translate(6px, -6px) scale(0.8);
}
.send:not(:disabled) .send-icon {
  animation: send-float 2.4s ease-in-out infinite;
}
.send:not(:disabled):hover .send-icon {
  animation: none;
}
@keyframes send-float {
  0%,
  100% {
    transform: translate(0, 0);
  }
  50% {
    transform: translate(1.5px, -1.5px);
  }
}
/* 触屏没有悬停：按下时弹一下，当前选中的按钮 / 标签图标持续轻微呼吸 */
.fab:active .fab-icon,
.tab-label:active .n-icon {
  transform: scale(0.8) rotate(8deg);
}
.fab.on .fab-icon,
:deep(.n-tabs-tab--active) .tab-label .n-icon {
  animation: icon-breathe 2.4s ease-in-out infinite;
}
@keyframes icon-breathe {
  0%,
  100% {
    transform: scale(1);
    filter: drop-shadow(0 0 0 transparent);
  }
  50% {
    transform: scale(1.18);
    filter: drop-shadow(0 0 5px #3d6b8c);
  }
}
@media (prefers-reduced-motion: reduce) {
  .session,
  .row,
  .empty-icon,
  .row.live .bubble::before,
  .row.live .bubble::after,
  .row.settled .bubble {
    animation: none;
  }
  .session,
  .session-icon {
    transition: none;
  }
  .session:hover,
  .session:active,
  .session:hover .session-icon {
    transform: none;
  }
  .fab-icon,
  .toggle-icon,
  .new-icon,
  .send-icon,
  .tab-label .n-icon {
    transition: none;
    animation: none !important;
  }
  .fab:hover .fab-icon,
  .tab-label:hover .n-icon,
  .fab:active .fab-icon,
  .tab-label:active .n-icon,
  .toggle:hover .toggle-icon,
  .toggle:active .toggle-icon,
  .new:hover .new-icon,
  .new:active .new-icon,
  .send:hover .send-icon,
  .send:active .send-icon {
    transform: none;
  }
  .send .send-icon {
    animation: none !important;
  }
  .toggle-icon.flip {
    transform: rotate(180deg) !important;
  }
}
.new {
  width: 100%;
  margin-bottom: 16px;
}
.session {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
  animation: session-in 0.35s ease backwards; /* backwards：延迟期间先保持透明 */
  transition: background 0.2s ease, transform 0.2s ease, color 0.2s ease;
}
@keyframes session-in {
  from {
    opacity: 0;
    transform: translateX(-12px);
  }
}
.session:hover {
  background: #eef2f6;
  transform: translateX(3px);
}
.session:active {
  transform: scale(0.98);
}
.session.active {
  background: linear-gradient(135deg, #e3edf5, #cfe0ee);
  color: #1c2733;
}
.session.active .time {
  color: #5b7387;
}
.session-text {
  min-width: 0;
}
.session-icon {
  flex-shrink: 0;
  margin-top: 2px;
  color: #4f7f99;
  transition: transform 0.25s ease, filter 0.25s ease;
}
.session:hover .session-icon {
  transform: scale(1.2) rotate(-8deg);
  filter: drop-shadow(0 0 4px #3d6b8c);
}
.session.active .session-icon {
  color: #3d6b8c;
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
  display: none !important; /* 空的 pane 自带 12px 顶部内边距，会把标签顶偏 */
}
.tab-switch :deep(.n-tabs-nav) {
  border-bottom: none;
}
.messages-wrap {
  position: relative; /* 流星雨绝对定位在它里面 */
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
}
.messages {
  position: relative; /* 盖在流星雨上面 */
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px;
}
.empty {
  text-align: center;
  color: #909399;
  margin-top: 48px;
}
/* 空会话的图标缓慢浮动（Inspira UI 的 Float 思路） */
.empty-icon {
  display: block;
  margin: 0 auto 8px;
  color: #4f7f99;
  animation: float 3s ease-in-out infinite;
}
@keyframes float {
  0%,
  100% {
    transform: translateY(0) rotate(-5deg);
  }
  50% {
    transform: translateY(-8px) rotate(5deg);
  }
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
  animation: row-in 0.3s ease; /* 新消息出现时淡入上滑一次 */
}
@keyframes row-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
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
  background: linear-gradient(135deg, #e3edf5, #c9dcec);
  color: #1c2733;
}
/* 流式输出中的那条回复（id 是 Infinity 的临时消息）：左边一道冷蓝色光条上下流动，末尾跟一个闪烁的光标
   （借鉴 Inspira UI 的 Typewriter Text 光标和 Border Beam 的流光）。回复结束换成真消息后自动消失 */
.row.live .bubble {
  position: relative;
  padding-left: 20px;
}
.row.live .bubble::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  background: linear-gradient(180deg, #0b0f14, #4f7f99, #8fb3cc, #4f7f99, #0b0f14);
  background-size: 100% 300%;
  animation: stream-beam 2.4s linear infinite; /* 放慢：和放慢的出字节奏匹配 */
}
.row.live .bubble::after {
  content: '';
  display: inline-block;
  width: 2px;
  height: 1.1em;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: #3d6b8c;
  animation: stream-caret 1.2s steps(1) infinite;
}
/* 输出完成后的样子：定格成一条稳定的冷蓝边 + 浅冷蓝底。这是所有模型回复的常驻样式（不靠前端记忆），
   所以刷新页面、切换会话、从别的页面回来，历史回复都保持这个效果；流式中的临时消息（.live）除外，它有自己的流光 */
.row.assistant:not(.live) .bubble {
  position: relative;
  padding-left: 20px;
  box-shadow: inset 4px 0 0 #4f7f99, 0 0 0 1px rgba(79, 127, 153, 0.25), 0 2px 10px rgba(61, 107, 140, 0.18);
  background: linear-gradient(135deg, #f4f7fa, #e9f0f6);
}
/* 只有刚流式输出完的那条（settled）才播一次「落定」闪光；动画结束后回到上面的常驻样式 */
.row.settled .bubble {
  animation: settle 0.8s ease;
}
@keyframes settle {
  0% {
    box-shadow: inset 4px 0 0 #8fb3cc, 0 0 0 1px rgba(143, 179, 204, 0.8), 0 0 18px rgba(143, 179, 204, 0.9);
  }
  100% {
    box-shadow: inset 4px 0 0 #4f7f99, 0 0 0 1px rgba(79, 127, 153, 0.25), 0 2px 10px rgba(61, 107, 140, 0.18);
  }
}
@keyframes stream-beam {
  to {
    background-position: 0 300%;
  }
}
@keyframes stream-caret {
  50% {
    opacity: 0;
  }
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
  /* 标签之间的间距（默认 36px，是组件内联的变量，覆盖不了）收小：改垫片宽度，360px 宽的手机上三个标签才放得下 */
  .tab-switch :deep(.n-tabs-tab-pad) {
    width: 14px;
  }
  .messages {
    padding: 12px;
  }
  /* 触屏没有悬停，点一下后 :hover 会「粘住」：会话项不再做悬停位移 */
  .session:hover {
    transform: none;
  }
  /* 回复气泡左边的光边占位收小，窄屏上文字区才不被挤 */
  .row.assistant .bubble,
  .row.live .bubble {
    padding-left: 14px;
  }
  /* 输入区贴底时避开 iPhone 底部横条 */
  .composer {
    padding: 8px 12px calc(8px + env(safe-area-inset-bottom));
  }
}
</style>
