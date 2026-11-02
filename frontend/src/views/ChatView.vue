<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createSession, listMessages, listSessions, openStream, streamMessage, uploadFile } from '../api'
import FileUploadButton from '../components/FileUploadButton.vue'
import MarkdownPreview from '../components/MarkdownPreview.vue'
import ModelPicker from '../components/ModelPicker.vue'
import PluginPanel from '../components/PluginPanel.vue'
import { buildSendOptions } from '../utils/options'
import { sendWithAttachment } from '../utils/sendFlow'
import { createTypewriter } from '../utils/typewriter'
import { formatSize, uploadErrorMessage } from '../utils/upload'

const sessions = ref([])
const currentId = ref(null)
const messages = ref([])
const draft = ref('')
const pendingFile = ref(null) // 已选好、还没上传的文件；点「发送」时才上传
const stage = ref('') // 发送过程中给用户看的一句话（比如「上传文件中…」）
const modelChoice = ref(null) // 选的模型和推理强度 { model, effort }；选项还没加载出来时是 null
const sending = ref(false)
const listEl = ref(null)

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
  pendingFile.value = null // 选好还没传的文件属于原来的会话，不能带到新会话里
  messages.value = []
  addMessages(await listMessages(id))
  const last = messages.value.length ? messages.value[messages.value.length - 1].id : 0
  closeStream = openStream(id, last, (m) => addMessages([m]))
}

async function newSession() {
  const created = await createSession()
  sessions.value.unshift(created)
  await selectSession(created.id)
}

async function send() {
  const content = draft.value.trim()
  const attachment = pendingFile.value
  const options = buildSendOptions(modelChoice.value) // 点发送这一刻的选择，随这句话一起透传给后端
  // 有文件时，没有文字也能发（只上传）；两样都没有就不动
  if ((!content && !attachment) || !currentId.value || sending.value) return
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
      ElMessage.success(`已上传 ${uploaded.name}，${formatSize(uploaded.size)}${renamed}`)
    }
    // 成功才清空；失败时保留文字和文件，用户可以直接重试
    draft.value = ''
    pendingFile.value = null
  } catch (e) {
    if (e.stage === 'upload') {
      ElMessage.error(`文件上传失败，文本未发送：${uploadErrorMessage(e.cause)}`)
    } else {
      // 发送阶段失败：文件其实已经传上去了，别让用户重传，只需重发文字
      if (attachment) pendingFile.value = null
      ElMessage.error(attachment ? '文件已上传，但消息发送失败，请重试' : '发送失败，请重试')
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
  let live = null
  // delta 到达的节奏不均匀，交给打字机匀到每一帧，每帧最多更新一次界面
  const typewriter = createTypewriter((text) => {
    live.content += text
    scrollToBottom()
  })
  const dropLive = () => {
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
        },
        delta: ({ text }) => {
          if (!live) {
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
  if (sessions.value.length) await selectSession(sessions.value[0].id)
  else await newSession()
})
onBeforeUnmount(() => closeStream && closeStream())
</script>

<template>
  <div class="chat">
    <aside class="sidebar">
      <el-button type="primary" class="new" @click="newSession">新建会话</el-button>
      <div
        v-for="s in sessions"
        :key="s.id"
        class="session"
        :class="{ active: s.id === currentId }"
        @click="selectSession(s.id)"
      >
        {{ s.title }}
        <span class="time">{{ new Date(s.created_at).toLocaleString() }}</span>
      </div>
    </aside>

    <main class="main">
      <div ref="listEl" class="messages">
        <div v-if="!messages.length" class="empty">发送第一条消息，开始对话</div>
        <div v-for="m in messages" :key="m.id" class="row" :class="m.role">
          <div class="bubble">{{ m.content }}</div>
        </div>
      </div>

      <div class="composer">
        <MarkdownPreview :text="draft" />
        <el-input
          v-model="draft"
          type="textarea"
          :rows="3"
          resize="none"
          placeholder="支持 Markdown，Ctrl+Enter 发送"
          @keydown.ctrl.enter.prevent="send"
        />
        <!-- 文本框下方一排：左边选文件；右边依次是模型、推理强度、发送，三者垂直居中对齐 -->
        <div class="actions">
          <FileUploadButton v-model="pendingFile" :disabled="sending" />
          <div class="right">
            <ModelPicker v-model="modelChoice" :disabled="sending" />
            <el-button
              type="primary"
              :loading="sending"
              :disabled="!draft.trim() && !pendingFile"
              @click="send"
            >
              {{ stage || '发送' }}
            </el-button>
          </div>
        </div>
      </div>
    </main>

    <PluginPanel :session-id="currentId" />
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  height: 100%;
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
.row {
  display: flex;
  margin-bottom: 16px;
}
.row.user {
  justify-content: flex-end;
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
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 20px;
  border-top: 1px solid #e4e7ed;
}
/* 按钮一排：左边选文件；右边是模型、强度、发送。整排垂直居中，所以高度不同的控件也上下对齐 */
.composer .actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.composer .actions .right {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
}
</style>
