// eureka 工作流进度的纯逻辑：状态、摘要、图标、拉取节奏。方便测试，不碰 DOM。
//
// 为什么用「拉取」而不是 SSE：这一步的进度很短（几个阶段、几秒），并且 EventSource 不能带自定义逻辑做退避；
// 用一个递增间隔的轮询，结束就停，实现最简单，也不占用后端的长连接线程（Django 开发服务器每个 SSE 占一个线程）。

export const STATUS_LABEL = { pending: '等待', running: '进行中', done: '完成', failed: '失败' }

/** done 和 failed 是终态。 */
export const isFinished = (status) => status === 'done' || status === 'failed'

/** 一个阶段的图标类别（对应 Element Plus 的 type 配色）。不认识的状态当等待。 */
export function stageIcon(status) {
  return { done: { kind: 'success' }, failed: { kind: 'danger' }, running: { kind: 'primary' } }[status] ?? { kind: 'info' }
}

/** 一句话摘要：做到哪一步了。 */
export function progressSummary(run) {
  const stages = run?.stages ?? []
  const total = stages.length
  const done = stages.filter((s) => s.status === 'done').length
  if (!total) return { done: 0, total: 0, text: '等待开始' }
  const failed = stages.find((s) => s.status === 'failed')
  if (failed) return { done, total, text: `阶段「${failed.title}」失败` }
  if (done === total) return { done, total, text: `全部 ${total} 个阶段已完成` }
  return { done, total, text: `${done} / ${total} 个阶段完成` }
}

/** 还要不要继续拉取：没结束（或者还没取到）就继续。 */
export const shouldPoll = (run) => !run || !isFinished(run.status)

/** 第 n 次拉取之后等多久（毫秒）：刚开始快，逐渐放慢，上限 3 秒。 */
export function nextDelay(attempt) {
  const n = Number.isFinite(attempt) && attempt > 0 ? attempt : 0
  return Math.min(500 + n * 500, 3000)
}
