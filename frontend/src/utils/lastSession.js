// 记住刷新前正在看的会话：聊天记录和工作流勾选都存在后端的会话里，前端只要回到同一个会话就都恢复了。
export const STORAGE_KEY = 'harness.lastSessionId'

// 从会话列表里选出刷新后要打开的那个：记住的还在列表里就用它，否则（没记过、已被删）用第一个；列表空返回 null
export function pickSession(sessions, savedId) {
  if (!sessions.length) return null
  return (sessions.find((s) => s.id === savedId) ?? sessions[0]).id
}

// localStorage 不可用（隐私模式等）或内容异常时都当作没记过，不影响使用
export function loadLastSession(storage = globalThis.localStorage) {
  try {
    return storage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

export function saveLastSession(id, storage = globalThis.localStorage) {
  try {
    storage.setItem(STORAGE_KEY, id)
  } catch {
    // 存不了就算了：只是刷新后回不到原来的会话
  }
}
