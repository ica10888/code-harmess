// 模型 / 强度选项的前端逻辑：node --test，不需要浏览器。
import assert from 'node:assert/strict'
import test from 'node:test'
import { effortIndex, effortFromIndex, resolveChoice, buildSendOptions } from './options.js'

const efforts = [
  { value: 'none', label: '关闭' },
  { value: 'low', label: '低' },
  { value: 'high', label: '高' },
  { value: 'max', label: '最高' },
]
const models = [{ value: 'deepseek-flash' }, { value: 'deepseek-v4-pro' }]
const server = { models, efforts, default_model: 'deepseek-flash', default_effort: 'low' }

test('滑块的下标和强度值互相转换，顺序就是后端给的从弱到强', () => {
  assert.equal(effortIndex(efforts, 'high'), 2)
  assert.equal(effortFromIndex(efforts, 3), 'max')
  assert.equal(effortFromIndex(efforts, effortIndex(efforts, 'none')), 'none')
})

test('找不到的强度当作 0，下标越界取两端，不会得到 undefined', () => {
  assert.equal(effortIndex(efforts, 'nope'), 0)
  assert.equal(effortFromIndex(efforts, -5), 'none')
  assert.equal(effortFromIndex(efforts, 99), 'max')
})

test('没有选过时用后端给的默认值', () => {
  assert.deepEqual(resolveChoice(server, null), { model: 'deepseek-flash', effort: 'low' })
})

test('之前存的选择仍然有效就沿用', () => {
  assert.deepEqual(resolveChoice(server, { model: 'deepseek-v4-pro', effort: 'max' }), {
    model: 'deepseek-v4-pro',
    effort: 'max',
  })
})

test('存的选择已经不在枚举里（官方下线了某个模型），回退到默认，不拿失效的值去发', () => {
  assert.deepEqual(resolveChoice(server, { model: 'old-model', effort: 'ultra' }), {
    model: 'deepseek-flash',
    effort: 'low',
  })
  assert.deepEqual(resolveChoice(server, { model: 'deepseek-v4-pro', effort: 'ultra' }), {
    model: 'deepseek-v4-pro',
    effort: 'low',
  })
})

test('后端默认值本身不在列表里时，退而取列表第一项', () => {
  const odd = { ...server, default_model: 'x', default_effort: 'y' }
  assert.deepEqual(resolveChoice(odd, null), { model: 'deepseek-flash', effort: 'none' })
})

test('发送时带上 model 和 effort 两个字段', () => {
  assert.deepEqual(buildSendOptions({ model: 'deepseek-v4-pro', effort: 'max' }), {
    model: 'deepseek-v4-pro',
    effort: 'max',
  })
})

test('选项还没加载出来时不带这两个字段，让后端用默认值，而不是发 undefined', () => {
  assert.deepEqual(buildSendOptions(null), {})
  assert.deepEqual(buildSendOptions({ model: '', effort: '' }), {})
})
