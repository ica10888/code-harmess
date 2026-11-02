import axios from 'axios'

// 同源（经 vite 代理），浏览器会自动带上 Django 的 session cookie
const http = axios.create({ baseURL: '/api', withCredentials: true })

export const listSessions = () => http.get('/sessions/').then((r) => r.data)
export const createSession = (title = '') => http.post('/sessions/', { title }).then((r) => r.data)
export const listMessages = (sessionId, after = 0) =>
  http.get(`/sessions/${sessionId}/messages/`, { params: { after } }).then((r) => r.data)
export const sendMessage = (sessionId, content, options = {}) =>
  http.post(`/sessions/${sessionId}/messages/`, { content, ...options }).then((r) => r.data)

/** 可选的模型和推理强度（后端写死的枚举）：{ models, efforts, default_model, default_effort }。 */
export const getOptions = () => http.get('/options/').then((r) => r.data)

/**
 * 订阅后端推送（SSE）。每收到一条消息就调用 onMessage(message)。
 * 返回一个函数，调用它即关闭连接。断线后浏览器会自动重连，并带上 Last-Event-ID。
 */
export function openStream(sessionId, afterId, onMessage) {
  const source = new EventSource(`/api/sessions/${sessionId}/stream/?after=${afterId}`)
  source.onmessage = (event) => onMessage(JSON.parse(event.data))
  return () => source.close()
}

/**
 * 发一句话，回复边生成边回来（POST + SSE，所以不能用 EventSource，改用 fetch 读流）。
 * 事件：user（存好的用户消息）→ delta（一段文字 {text}）… → done（存好的完整回复）。
 * handlers = { user, delta, done }，各是 (data) => void。失败时抛异常（网络、4xx/5xx）。
 * options = { model, effort }，都可选，不给就用后端默认。
 */
export async function streamMessage(sessionId, content, handlers, options = {}) {
  const res = await fetch(`/api/sessions/${sessionId}/messages/stream/`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    // options = { model, effort }：这一句话用哪个模型、多强的推理，原样透传给后端（见 utils/options.js 的 buildSendOptions）
    body: JSON.stringify({ content, ...options }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // 一个事件以空行结束；最后那段可能还不完整，留在 buffer 里等下一块
    const frames = buffer.split('\n\n')
    buffer = frames.pop()
    for (const frame of frames) {
      const event = /^event: (.+)$/m.exec(frame)?.[1]
      const data = /^data: (.+)$/m.exec(frame)?.[1]
      if (event && data && handlers[event]) handlers[event](JSON.parse(data))
    }
  }
}

/** 上传一个文件到当前会话的工作区；onProgress(0~100) 报告进度。返回 {name, size}（重名时 name 会被改成 "a (1).txt"）。 */
export const uploadFile = (sessionId, file, onProgress) => {
  const form = new FormData()
  form.append('file', file)
  return http
    .post(`/sessions/${sessionId}/files/`, form, {
      onUploadProgress: (e) => e.total && onProgress?.(Math.round((e.loaded / e.total) * 100)),
    })
    .then((r) => r.data)
}

// --- 插件（skill）管理：每个会话一份，文件存在后端 <存储路径>/input/<会话id>/skills/ ---
export const listPlugins = (sessionId) => http.get(`/sessions/${sessionId}/plugins/`).then((r) => r.data)
/** 上传一个 SKILL.md 新增插件，返回新建的插件（默认不勾选）。 */
export const addPlugin = (sessionId, file) => {
  const form = new FormData()
  form.append('file', file)
  return http.post(`/sessions/${sessionId}/plugins/`, form).then((r) => r.data)
}
/** 勾选 / 取消勾选，返回更新后的插件。name 是插件名称（后端的目录名）。 */
export const setPluginEnabled = (sessionId, name, enabled) =>
  http.patch(`/sessions/${sessionId}/plugins/${encodeURIComponent(name)}/`, { enabled }).then((r) => r.data)
export const deletePlugin = (sessionId, name) =>
  http.delete(`/sessions/${sessionId}/plugins/${encodeURIComponent(name)}/`)

// --- 工作流管理：和插件完全对称，文件存在后端 <存储路径>/input/<会话id>/workflow/ ---
/** 详情：多一个 content（文件全文），点名称看内容时用。 */
export const getPlugin = (sessionId, name) =>
  http.get(`/sessions/${sessionId}/plugins/${encodeURIComponent(name)}/`).then((r) => r.data)
export const getWorkflow = (sessionId, name) =>
  http.get(`/sessions/${sessionId}/workflows/${encodeURIComponent(name)}/`).then((r) => r.data)
export const listWorkflows = (sessionId) => http.get(`/sessions/${sessionId}/workflows/`).then((r) => r.data)
export const addWorkflow = (sessionId, file) => {
  const form = new FormData()
  form.append('file', file)
  return http.post(`/sessions/${sessionId}/workflows/`, form).then((r) => r.data)
}
export const setWorkflowEnabled = (sessionId, name, enabled) =>
  http.patch(`/sessions/${sessionId}/workflows/${encodeURIComponent(name)}/`, { enabled }).then((r) => r.data)
export const deleteWorkflow = (sessionId, name) =>
  http.delete(`/sessions/${sessionId}/workflows/${encodeURIComponent(name)}/`)
