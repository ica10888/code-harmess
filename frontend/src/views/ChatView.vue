<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createSession, listMessages, listSessions, openStream, streamMessage } from '../api'
import { createTypewriter } from '../utils/typewriter'

const sessions = ref([])
const currentId = ref(null)
const messages = ref([])
const draft = ref('')
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
  if (!content || !currentId.value || sending.value) return
  sending.value = true
  draft.value = ''
  // 正在长出来的回复：先用一条临时消息（id 取 Infinity，排在最后），done 到来时换成存好的真消息。
  // live 必须是 messages.value 里取回的响应式代理：改 push 之前的原对象，Vue 看不到，界面不会刷新。
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
    await streamMessage(currentId.value, content, {
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
    })
  } catch {
    // 流中途断了：去掉半截的临时消息，把没发成功的话放回输入框；已存的消息以服务器为准，刷新就能看到
    dropLive()
    draft.value = content
    ElMessage.error('发送失败，请重试')
  } finally {
    sending.value = false
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
        <el-input
          v-model="draft"
          type="textarea"
          :rows="3"
          resize="none"
          placeholder="输入内容，Ctrl+Enter 发送"
          @keydown.ctrl.enter.prevent="send"
        />
        <el-button type="primary" :loading="sending" :disabled="!draft.trim()" @click="send">发送</el-button>
      </div>
    </main>
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
  align-items: flex-end;
  gap: 8px;
  padding: 16px 20px;
  border-top: 1px solid #e4e7ed;
}
.composer .el-textarea {
  flex: 1;
}
</style>
