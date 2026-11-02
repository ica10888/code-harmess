<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createSession, listMessages, listSessions, openStream, sendMessage } from '../api'

const sessions = ref([])
const currentId = ref(null)
const messages = ref([])
const draft = ref('')
const sending = ref(false)
const listEl = ref(null)

let closeStream = null

// 发送的回复和 SSE 推送可能带来同一条消息，按 id 去重
function addMessages(incoming) {
  const known = new Set(messages.value.map((m) => m.id))
  for (const m of incoming) if (!known.has(m.id)) messages.value.push(m)
  messages.value.sort((a, b) => a.id - b.id)
  nextTick(() => listEl.value && (listEl.value.scrollTop = listEl.value.scrollHeight))
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
  try {
    addMessages(await sendMessage(currentId.value, content))
    draft.value = ''
  } catch {
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
