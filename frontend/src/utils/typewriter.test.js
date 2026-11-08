// 打字机的调度逻辑测试：用 Node 自带的 node:test 跑（cd frontend && npm test），不需要浏览器，也不装测试库。
import assert from 'node:assert/strict'
import test from 'node:test'
import { createTypewriter, FRAMES_PER_STEP } from './typewriter.js'

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
    // 一「步」= 放出一批文字；为了放慢，每 FRAMES_PER_STEP 帧才放一次，所以一步要刷新这么多帧
    frame() {
      for (let i = 0; i < FRAMES_PER_STEP; i++) {
        const fn = pending
        pending = null
        if (fn) fn()
      }
    },
    // 只刷新一帧（用来检查「没到点不放字」）
    rawFrame() {
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

test('积压很少时每步只放一小撮，像打字', () => {
  const t = setup()
  t.typewriter.push('abcdef')
  t.frame()
  t.frame()
  assert.deepEqual(t.out, ['abc', 'def'])
})

test('没到点的帧不放字：放慢展开速度', () => {
  const t = setup()
  t.typewriter.push('abc')
  for (let i = 0; i < FRAMES_PER_STEP - 1; i++) t.rawFrame()
  assert.deepEqual(t.out, [])
  t.rawFrame()
  assert.deepEqual(t.out, ['abc'])
})

test('积压很多时每步多放一些，慢慢追上，不会落后太多', () => {
  const t = setup()
  t.typewriter.push('x'.repeat(900))
  t.frame()
  assert.equal(t.out[0].length, 15) // 900 / 60
  let steps = 1
  while (t.waiting) {
    t.frame()
    steps++
  }
  assert.equal(t.out.join('').length, 900)
  assert.ok(steps <= 300, `用了 ${steps} 步`) // 有下限，最多 900 / 3 步；每步 3 帧，约 15 秒内追完
})

test('drain 不会一次放完：按节奏放完后才算结束', async () => {
  const t = setup()
  t.typewriter.push('abcdef')
  let drained = false
  const done = t.typewriter.drain().then(() => (drained = true))
  t.frame()
  await Promise.resolve()
  assert.equal(drained, false) // 只放了 'abc'
  assert.deepEqual(t.out, ['abc'])
  t.frame()
  await done
  assert.equal(t.out.join(''), 'abcdef')
})

test('没有积压时 drain 立刻结束', async () => {
  const t = setup()
  await t.typewriter.drain()
})

test('drain 等待期间 cancel，也会结束等待（不会一直挂着）', async () => {
  const t = setup()
  t.typewriter.push('abc')
  const done = t.typewriter.drain()
  t.typewriter.cancel()
  await done
  assert.deepEqual(t.out, [])
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
  assert.equal(t.out.join(''), 'hel') // 取消前只放出了第一批（3 个字）
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
