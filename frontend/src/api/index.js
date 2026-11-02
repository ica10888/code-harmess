import axios from 'axios'

// 同源（经 vite 代理），浏览器会自动带上 Django 的 session cookie
const http = axios.create({ baseURL: '/api', withCredentials: true })

export const listSessions = () => http.get('/sessions/').then((r) => r.data)
export const createSession = (title = '') => http.post('/sessions/', { title }).then((r) => r.data)
export const listMessages = (sessionId, after = 0) =>
  http.get(`/sessions/${sessionId}/messages/`, { params: { after } }).then((r) => r.data)
export const sendMessage = (sessionId, content) =>
  http.post(`/sessions/${sessionId}/messages/`, { content }).then((r) => r.data)

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
 */
export async function streamMessage(sessionId, content, handlers) {
  const res = await fetch(`/api/sessions/${sessionId}/messages/stream/`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
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
