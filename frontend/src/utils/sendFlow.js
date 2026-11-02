// 发送流程：有文件时，先上传、等上传结束，再发送文本。纯函数，依赖都从参数传进来，方便测试。

/**
 * 严格按顺序：先 upload(file)，await 它完成，之后才 send(text)。两步不并行。
 * - 上传失败：抛出，且不会发送文本（e.stage === 'upload'）。
 * - 发送失败：抛出（e.stage === 'send'）；此时文件已经上传成功，调用方可以据此提示。
 * - 没有文件：直接发送；没有文字：只上传，不发空文本。
 * 原始错误放在 e.cause 里。返回 { uploaded }：上传结果，没传文件时是 null。
 */
export async function sendWithAttachment({ file, text, upload, send }) {
  let uploaded = null
  if (file) {
    try {
      uploaded = await upload(file)
    } catch (cause) {
      throw stageError('upload', cause)
    }
  }
  if (text) {
    try {
      await send(text)
    } catch (cause) {
      throw stageError('send', cause)
    }
  }
  return { uploaded }
}

function stageError(stage, cause) {
  const e = new Error(`${stage} failed: ${cause?.message ?? cause}`)
  e.stage = stage
  e.cause = cause
  return e
}

/**
 * 拖拽进来的是文件夹吗？entry 是 DataTransferItem.webkitGetAsEntry() 的结果。
 * 文件选择框（点 + 号）本身选不了文件夹，只有拖拽才会遇到。
 */
export function isDirectoryEntry(entry) {
  return Boolean(entry && entry.isDirectory)
}
