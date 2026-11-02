// 打字机的调度逻辑测试：用 Node 自带的 node:test 跑（cd frontend && npm test），不需要浏览器，也不装测试库。
import assert from 'node:assert/strict'
import test from 'node:test'
import { createTypewriter } from './typewriter.js'

// 假的「屏幕刷新」：schedule 只是把回调存起来，frame() 相当于刷新一帧
function setup() {
  let pending = null
  const frames = {
    schedule: (fn) => ((pending = fn), 1),
    cancel: () => (pending = null),
  }
  const out = []
  const typewriter = createTypewriter((text) => out.push(text), frames)
  return {
    typewriter,
    out,
    frame() {
      const fn = pending
      pending = null
      if (fn) fn()
    },
    get waiting() {
      return pending !== null
    },
  }
}

test('收到文字不立刻显示，等下一帧', () => {
  const t = setup()
  t.typewriter.push('abc')
  assert.deepEqual(t.out, [])
  t.frame()
  assert.equal(t.out.length, 1)
})

test('同一帧里来了很多段，也只更新一次界面', () => {
  const t = setup()
  for (let i = 0; i < 50; i++) t.typewriter.push('x')
  t.frame()
  assert.equal(t.out.length, 1)
})

test('积压很少时一帧一个字，像打字', () => {
  const t = setup()
  t.typewriter.push('abc')
  t.frame()
  t.frame()
  t.frame()
  assert.deepEqual(t.out, ['a', 'b', 'c'])
})

test('积压很多时每帧多放一些，很快追上，不会落后太多', () => {
  const t = setup()
  t.typewriter.push('x'.repeat(800))
  t.frame()
  assert.equal(t.out[0].length, 100) // 800 / 8
  let frames = 1
  while (t.waiting) {
    t.frame()
    frames++
  }
  assert.equal(t.out.join('').length, 800)
  assert.ok(frames < 60, `用了 ${frames} 帧`) // 60fps 下不到 1 秒
})

test('放完就不再排下一帧', () => {
  const t = setup()
  t.typewriter.push('ab')
  t.frame()
  t.frame()
  assert.equal(t.waiting, false)
  t.typewriter.push('c') // 之后再来新文字，会重新开始
  assert.equal(t.waiting, true)
})

test('finish 立刻放出剩下的全部，顺序不变，并取消排好的下一帧', () => {
  const t = setup()
  t.typewriter.push('hello ')
  t.frame()
  t.typewriter.push('world')
  t.typewriter.finish()
  assert.equal(t.out.join(''), 'hello world')
  assert.equal(t.waiting, false)
})

test('cancel 丢掉还没放出的文字', () => {
  const t = setup()
  t.typewriter.push('hello')
  t.frame()
  t.typewriter.cancel()
  t.typewriter.finish()
  assert.equal(t.out.join(''), 'h')
  assert.equal(t.waiting, false)
})

test('不会把一个 emoji 拆在两帧里', () => {
  const t = setup()
  t.typewriter.push('😀😀😀')
  while (true) {
    t.frame()
    if (!t.waiting) break
  }
  assert.equal(t.out.join(''), '😀😀😀')
  for (const piece of t.out) assert.doesNotMatch(piece, /[\ud800-\udbff]$/) // 不能以半个 emoji 结尾
})

test('不管怎么分段，拼起来都和收到的一致', () => {
  const t = setup()
  const pieces = ['你好', '，', 'world', '\n\n', '再见 😀', '!'.repeat(30)]
  for (const p of pieces) {
    t.typewriter.push(p)
    t.frame()
  }
  t.typewriter.finish()
  assert.equal(t.out.join(''), pieces.join(''))
})
