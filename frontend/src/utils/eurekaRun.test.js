// eureka 工作流进度的前端逻辑：node --test，不需要浏览器。
import assert from 'node:assert/strict'
import test from 'node:test'
import { isFinished, progressSummary, stageIcon, shouldPoll, nextDelay, STATUS_LABEL, extractLinks, jumpDelayMs } from './eurekaRun.js'

const stage = (key, status, extra = {}) => ({ key, title: key.toUpperCase(), status, error: '', logs: [], ...extra })
const run = (status, stages) => ({ id: 1, plugin: 'code-review', status, error: '', stages })

test('只有 done 和 failed 算结束，pending / running 还在进行', () => {
  assert.equal(isFinished('done'), true)
  assert.equal(isFinished('failed'), true)
  assert.equal(isFinished('pending'), false)
  assert.equal(isFinished('running'), false)
  assert.equal(isFinished(undefined), false)
})

test('每种状态有一个中文名，用于显示', () => {
  assert.deepEqual(Object.keys(STATUS_LABEL).sort(), ['done', 'failed', 'pending', 'running'])
  assert.equal(STATUS_LABEL.done, '完成')
  assert.equal(STATUS_LABEL.failed, '失败')
})

test('进度摘要：已完成几个阶段、一共几个', () => {
  const r = run('running', [stage('a', 'done'), stage('b', 'running'), stage('c', 'pending')])
  assert.deepEqual(progressSummary(r), { done: 1, total: 3, text: '1 / 3 个阶段完成' })
})

test('全部完成的摘要', () => {
  const r = run('done', [stage('a', 'done'), stage('b', 'done')])
  assert.equal(progressSummary(r).text, '全部 2 个阶段已完成')
})

test('失败时摘要指出是哪个阶段失败', () => {
  const r = run('failed', [stage('a', 'done'), stage('b', 'failed', { error: '炸了' }), stage('c', 'pending')])
  assert.match(progressSummary(r).text, /B/)
  assert.match(progressSummary(r).text, /失败/)
})

test('没有阶段（异常数据）也不会报错', () => {
  assert.deepEqual(progressSummary(run('pending', [])), { done: 0, total: 0, text: '等待开始' })
  assert.deepEqual(progressSummary(null), { done: 0, total: 0, text: '等待开始' })
})

test('每种阶段状态对应一个图标和一个样式类', () => {
  assert.equal(stageIcon('done').kind, 'success')
  assert.equal(stageIcon('failed').kind, 'danger')
  assert.equal(stageIcon('running').kind, 'primary')
  assert.equal(stageIcon('pending').kind, 'info')
  assert.equal(stageIcon('???').kind, 'info') // 不认识的当等待
})

test('还没结束才需要继续拉取进度', () => {
  assert.equal(shouldPoll({ status: 'pending' }), true)
  assert.equal(shouldPoll({ status: 'running' }), true)
  assert.equal(shouldPoll({ status: 'done' }), false)
  assert.equal(shouldPoll({ status: 'failed' }), false)
  assert.equal(shouldPoll(null), true) // 还没取到：继续取
})

test('拉取间隔：刚开始快，之后逐渐放慢，有上限，不会无限变慢', () => {
  const delays = [0, 1, 2, 3, 4, 10, 100].map(nextDelay)
  assert.ok(delays[0] <= 1000)
  assert.ok(delays.every((d, i) => i === 0 || d >= delays[i - 1])) // 单调不减
  assert.equal(delays[delays.length - 1], 3000) // 上限 3 秒
})

test('拉取间隔对非法入参也给出合理值', () => {
  assert.equal(nextDelay(-5), nextDelay(0))
  assert.equal(nextDelay(NaN), nextDelay(0))
})

test('extractLinks：按出现顺序收集所有阶段日志里的链接，去重', () => {
  const run = {
    stages: [
      { logs: ['部署完成：http://127.0.0.1:5200，请访问', '无链接'] },
      { logs: ['again http://127.0.0.1:5200 和 https://a.com/x'] },
    ],
  }
  assert.deepEqual(extractLinks(run), ['http://127.0.0.1:5200', 'https://a.com/x'])
  assert.deepEqual(extractLinks(null), [])
})

test('jumpDelayMs：工作流完成后，等面板动画播完再跳转', () => {
  // 面板入场延迟 gap 秒 + 入场动画 + 阶段依次滑入 + 完成态的收尾，再留一小会儿让人看清
  const base = jumpDelayMs(0, 3)
  assert.ok(base > 0)
  assert.equal(jumpDelayMs(1.2, 3) - base, 1200) // 入场延迟越长，等得越久
  assert.ok(jumpDelayMs(0, 5) > jumpDelayMs(0, 3)) // 阶段越多，滑入动画越长
})

test('jumpDelayMs：不认识的参数当成 0，不会得到 NaN', () => {
  assert.ok(Number.isFinite(jumpDelayMs(undefined, undefined)))
  assert.ok(Number.isFinite(jumpDelayMs(NaN, -1)))
})
