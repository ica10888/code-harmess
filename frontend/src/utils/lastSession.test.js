// 刷新后恢复会话的选择逻辑：node --test，不需要浏览器。
import assert from 'node:assert/strict'
import test from 'node:test'
import { loadLastSession, pickSession, saveLastSession } from './lastSession.js'

const list = [{ id: 'a' }, { id: 'b' }, { id: 'c' }]

test('记住的会话还在列表里，就打开它', () => {
  assert.equal(pickSession(list, 'b'), 'b')
})

test('没记过或记住的已被删除，打开第一个', () => {
  assert.equal(pickSession(list, null), 'a')
  assert.equal(pickSession(list, 'gone'), 'a')
})

test('列表为空返回 null，由调用方新建会话', () => {
  assert.equal(pickSession([], 'a'), null)
})

test('存取一个会话 id', () => {
  const data = {}
  const storage = { getItem: (k) => data[k] ?? null, setItem: (k, v) => (data[k] = v) }
  assert.equal(loadLastSession(storage), null)
  saveLastSession('b', storage)
  assert.equal(loadLastSession(storage), 'b')
})

test('localStorage 抛异常时不崩溃', () => {
  const broken = {
    getItem: () => {
      throw new Error('denied')
    },
    setItem: () => {
      throw new Error('denied')
    },
  }
  assert.equal(loadLastSession(broken), null)
  assert.doesNotThrow(() => saveLastSession('a', broken))
})
