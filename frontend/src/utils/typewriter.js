// 打字机：把后端一段一段推来的文字，匀到每个屏幕刷新帧里显示。
//
// 为什么需要它：delta 到达的节奏不均匀（有时一毫秒来好几段，有时停一秒），
// 每来一段就改一次界面，要么一帧里重复渲染很多次造成卡顿，要么一卡一卡地蹦。
// 这里让「收到」和「显示」脱开：push 只往缓冲里放，每一帧（约 16ms）最多更新一次界面。
//
// 为了让人看清文字「长出来」的过程，故意放慢：每 FRAMES_PER_STEP 帧才放一批（60Hz 下 2 帧约 33ms 一批）。
// 每批放多少：积压很少时一个字（有打字的感觉）；积压多了按比例放（缓冲 / CATCH_UP_STEPS）。
// 按比例放是指数衰减，尾巴会拖得很长，所以再设个下限 MIN_PER_STEP：
// 这样 N 个字最多 N / MIN_PER_STEP 批，不管网络多快，显示都不会越落越远。

export const FRAMES_PER_STEP = 2
const CATCH_UP_STEPS = 60
const MIN_PER_STEP = 3

const browserFrames = {
  schedule: (fn) => requestAnimationFrame(fn),
  cancel: (id) => cancelAnimationFrame(id),
}

/**
 * @param {(text: string) => void} emit  每帧调用一次，参数是这一帧要显示的新文字
 * @param {{schedule: Function, cancel: Function}} frames  帧调度，默认用浏览器的 requestAnimationFrame；测试里换成假的
 */
export function createTypewriter(emit, frames = browserFrames) {
  let buffer = ''
  let timer = null
  let skipped = 0 // 已经跳过的帧数；攒够 FRAMES_PER_STEP 才放一批
  let waiters = [] // drain() 在等的人：缓冲放空（或取消）时一起叫醒

  const wake = () => {
    const list = waiters
    waiters = []
    list.forEach((resolve) => resolve())
  }

  function tick() {
    timer = null
    if (!buffer) return wake()
    if (++skipped < FRAMES_PER_STEP) {
      timer = frames.schedule(tick)
      return
    }
    skipped = 0
    // 按码点切：不能把一个 emoji 的两个半边拆在两批里，否则中间会闪出乱码
    const chars = Array.from(buffer)
    const count = Math.max(chars.length < MIN_PER_STEP ? chars.length : MIN_PER_STEP, Math.ceil(chars.length / CATCH_UP_STEPS))
    emit(chars.slice(0, count).join(''))
    buffer = chars.slice(count).join('')
    if (buffer) timer = frames.schedule(tick)
    else wake()
  }

  return {
    /** 收到一段新文字：放进缓冲，必要时排上下一帧 */
    push(text) {
      buffer += text
      if (timer === null) timer = frames.schedule(tick)
    },
    /** 等缓冲按节奏放完：用在回复结束时，让用户把已经收到的文字看完，再换成存好的真消息。没有积压就立刻返回 */
    drain() {
      if (!buffer) return Promise.resolve()
      return new Promise((resolve) => waiters.push(resolve))
    },
    /** 结束：立刻把剩下的全部放出来（不再等动画），并取消排好的帧 */
    finish() {
      if (timer !== null) frames.cancel(timer)
      timer = null
      if (buffer) emit(buffer)
      buffer = ''
      wake()
    },
    /** 出错或切换会话：丢掉还没显示的文字 */
    cancel() {
      if (timer !== null) frames.cancel(timer)
      timer = null
      buffer = ''
      wake()
    },
  }
}
