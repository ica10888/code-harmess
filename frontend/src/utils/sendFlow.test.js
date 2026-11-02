// 「先上传文件，再发送文本」的流程测试：node --test，不需要浏览器。
import assert from 'node:assert/strict'
import test from 'node:test'
import { isDirectoryEntry, sendWithAttachment } from './sendFlow.js'

const file = (name = 'a.txt', size = 10) => ({ name, size, type: 'text/plain' })

// 记录调用顺序的假依赖；upload/send 可以控制成功、失败、延迟
function setup({ uploadError = null, sendError = null, uploadDelay = 0 } = {}) {
  const log = []
  return {
    log,
    deps: {
      upload: async (f) => {
        log.push('upload:start')
        if (uploadDelay) await new Promise((r) => setTimeout(r, uploadDelay))
        if (uploadError) {
          log.push('upload:fail')
          throw uploadError
        }
        log.push('upload:done')
        return { name: f.name, size: f.size }
      },
      send: async (text) => {
        log.push(`send:${text}`)
        if (sendError) throw sendError
      },
    },
  }
}

test('有文件时：先上传，等上传完成，才发送文本（严格先后，不并行）', async () => {
  const { log, deps } = setup({ uploadDelay: 30 })
  await sendWithAttachment({ file: file(), text: '请分析', ...deps })
  assert.deepEqual(log, ['upload:start', 'upload:done', 'send:请分析'])
})

test('上传还没结束时，文本绝不会提前发出', async () => {
  const { log, deps } = setup({ uploadDelay: 50 })
  const pending = sendWithAttachment({ file: file(), text: 'hi', ...deps })
  await new Promise((r) => setTimeout(r, 20)) // 上传进行到一半
  assert.deepEqual(log, ['upload:start']) // 此刻文本一定还没发
  await pending
  assert.deepEqual(log, ['upload:start', 'upload:done', 'send:hi'])
})

test('上传失败：不发送文本，错误交给调用方，并标明是上传阶段失败', async () => {
  const boom = new Error('413')
  const { log, deps } = setup({ uploadError: boom })
  await assert.rejects(
    () => sendWithAttachment({ file: file(), text: 'hi', ...deps }),
    (e) => e.stage === 'upload' && e.cause === boom,
  )
  assert.deepEqual(log, ['upload:start', 'upload:fail']) // 没有 send
})

test('发送失败：标明是发送阶段失败（文件此时已经上传成功）', async () => {
  const boom = new Error('net')
  const { deps } = setup({ sendError: boom })
  await assert.rejects(
    () => sendWithAttachment({ file: file(), text: 'hi', ...deps }),
    (e) => e.stage === 'send' && e.cause === boom,
  )
})

test('没有文件：直接发送文本，不碰上传', async () => {
  const { log, deps } = setup()
  await sendWithAttachment({ file: null, text: 'hi', ...deps })
  assert.deepEqual(log, ['send:hi'])
})

test('返回上传结果，方便界面提示（含重名改名）', async () => {
  const { deps } = setup()
  const res = await sendWithAttachment({ file: file('x.csv', 5), text: 'hi', ...deps })
  assert.deepEqual(res.uploaded, { name: 'x.csv', size: 5 })
  assert.equal((await sendWithAttachment({ file: null, text: 'hi', ...deps })).uploaded, null)
})

test('只有文件、没有文字：只上传，不发送空文本', async () => {
  const { log, deps } = setup()
  await sendWithAttachment({ file: file(), text: '', ...deps })
  assert.deepEqual(log, ['upload:start', 'upload:done'])
})

// --- 文件夹识别 ---

test('文件夹条目被识别出来（拖拽时 webkitGetAsEntry 给出 isDirectory）', () => {
  assert.equal(isDirectoryEntry({ isDirectory: true }), true)
  assert.equal(isDirectoryEntry({ isDirectory: false, isFile: true }), false)
})

test('没有条目信息时，不误判为文件夹', () => {
  assert.equal(isDirectoryEntry(null), false)
  assert.equal(isDirectoryEntry(undefined), false)
})
