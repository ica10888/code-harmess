// 打字机：把后端一段一段推来的文字，匀到每个屏幕刷新帧里显示。
//
// 为什么需要它：delta 到达的节奏不均匀（有时一毫秒来好几段，有时停一秒），
// 每来一段就改一次界面，要么一帧里重复渲染很多次造成卡顿，要么一卡一卡地蹦。
// 这里让「收到」和「显示」脱开：push 只往缓冲里放，每一帧（约 16ms）最多更新一次界面。
//
// 每帧放多少：积压很少时一帧一个字（有打字的感觉）；积压多了按比例放（缓冲 / 8），
// 这样不管网络多快，显示大约 8 帧（约 0.13 秒）内追上，不会越落越远。

const CATCH_UP_FRAMES = 8

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

  function tick() {
    timer = null
    if (!buffer) return
    // 按码点切：不能把一个 emoji 的两个半边拆在两帧里，否则中间会闪出乱码
    const chars = Array.from(buffer)
    const count = Math.max(1, Math.ceil(chars.length / CATCH_UP_FRAMES))
    emit(chars.slice(0, count).join(''))
    buffer = chars.slice(count).join('')
    if (buffer) timer = frames.schedule(tick)
  }

  return {
    /** 收到一段新文字：放进缓冲，必要时排上下一帧 */
    push(text) {
      buffer += text
      if (timer === null) timer = frames.schedule(tick)
    },
    /** 结束：立刻把剩下的全部放出来（不再等动画），并取消排好的帧 */
    finish() {
      if (timer !== null) frames.cancel(timer)
      timer = null
      if (buffer) emit(buffer)
      buffer = ''
    },
    /** 出错或切换会话：丢掉还没显示的文字 */
    cancel() {
      if (timer !== null) frames.cancel(timer)
      timer = null
      buffer = ''
    },
  }
}
