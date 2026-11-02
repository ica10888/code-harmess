// 插件列表的前端逻辑：node --test，不需要浏览器。插件按「名称」标识（它就是后端的目录名）。
import assert from 'node:assert/strict'
import test from 'node:test'
import { checkSkillFile, checkedPlugins, pluginErrorMessage, removeByName, replaceByName } from './plugins.js'

const list = [
  { name: 'alpha', enabled: true },
  { name: 'beta', enabled: false },
  { name: 'gamma', enabled: true },
]

test('已勾选的插件：按列表顺序给出名称', () => {
  assert.deepEqual(checkedPlugins(list), ['alpha', 'gamma'])
  assert.deepEqual(checkedPlugins([]), [])
})

test('用服务器返回的新状态替换同名的一项，其余不动，也不改原数组', () => {
  const next = replaceByName(list, { name: 'beta', enabled: true })
  assert.deepEqual(next.map((p) => p.enabled), [true, true, true])
  assert.equal(list[1].enabled, false)
})

test('替换一个不存在的名称，列表保持原样', () => {
  assert.deepEqual(replaceByName(list, { name: 'nope', enabled: true }), list)
})

test('删除指定名称的一项，不改原数组', () => {
  assert.deepEqual(removeByName(list, 'beta').map((p) => p.name), ['alpha', 'gamma'])
  assert.equal(list.length, 3)
  assert.deepEqual(removeByName(list, 'nope'), list)
})

test('只要文件名以 .md 结尾就可以，不看大小写，也不看内容', () => {
  assert.equal(checkSkillFile({ name: 'SKILL.md', size: 500 }), null)
  assert.equal(checkSkillFile({ name: 'my-skill.MD', size: 500 }), null)
  assert.equal(checkSkillFile({ name: '我的笔记.md', size: 500 }), null)
  assert.equal(checkSkillFile({ name: 'a.b.c.md', size: 500 }), null)
})

test('不是 .md 的文件要拒绝，并说明原因', () => {
  for (const name of ['a.txt', 'a.markdown', 'a', 'a.md.exe', '.md']) {
    assert.match(checkSkillFile({ name, size: 500 }), /\.md/, name)
  }
})

test('空文件也允许（对内容没有任何要求）；大小只受通用上传上限约束', () => {
  assert.equal(checkSkillFile({ name: 'empty.md', size: 0 }), null)
  assert.equal(checkSkillFile({ name: 'a.md', size: 100 * 1024 }), null) // 100KB 不再是上限
  assert.match(checkSkillFile({ name: 'a.md', size: 21 * 1024 * 1024 }), /太大/)
})

test('没有选文件时提示去选择', () => {
  assert.match(checkSkillFile(undefined), /选择/)
})

test('后端给了原因就显示后端的，没给就按状态码说一句', () => {
  assert.equal(pluginErrorMessage({ response: { status: 409, data: { detail: '已经有叫 a 的插件' } } }), '已经有叫 a 的插件')
  assert.match(pluginErrorMessage({ response: { status: 409, data: {} } }), /同名/)
  assert.match(pluginErrorMessage({ response: { status: 404, data: {} } }), /不存在|刷新/)
  assert.match(pluginErrorMessage(new Error('Network Error')), /网络/)
})
