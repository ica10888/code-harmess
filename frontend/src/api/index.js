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
