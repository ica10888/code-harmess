// 插件列表的前端逻辑：node --test，不需要浏览器。插件按「名称」标识（它就是后端的目录名）。
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  checkSkillFile,
  pluginErrorMessage,
  removeByName,
} from './plugins.js'

const list = [{ name: 'alpha' }, { name: 'beta' }, { name: 'gamma' }]

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

// --- 默认插件：所有会话都有，不能删除 ---

test('后端拒绝删除默认插件（403）时，给出说得清楚的提示', () => {
  assert.match(pluginErrorMessage({ response: { status: 403, data: {} } }), /默认/)
})

// --- 内容检查：skill_selector 要用 frontmatter 里的 name 和 description ---

import { checkSkillContent } from './plugins.js'

test('有合规 frontmatter（name + description）的内容通过', () => {
  assert.equal(checkSkillContent('---\nname: code-review\ndescription: 审查代码\n---\n\n正文'), null)
  assert.equal(checkSkillContent('---\r\nname: a\r\ndescription: d\r\n---\r\nbody'), null) // Windows 换行
  assert.equal(checkSkillContent('﻿---\nname: a\ndescription: d\n---\nbody'), null) // 带 BOM
  assert.equal(checkSkillContent("---\nname: 'quoted'\ndescription: \"带引号\"\n---\n"), null)
})

test('没有 frontmatter 的内容，说清楚要怎么写', () => {
  const message = checkSkillContent('# 只有正文\n')
  assert.match(message, /frontmatter/)
  assert.match(message, /name/)
  assert.match(message, /description/)
})

test('frontmatter 没有用 --- 闭合', () => {
  assert.match(checkSkillContent('---\nname: a\ndescription: d\n'), /结束|闭合|---/)
})

test('缺 name 或缺 description，分别指出缺哪个', () => {
  assert.match(checkSkillContent('---\ndescription: d\n---\nbody'), /name/)
  assert.match(checkSkillContent('---\nname: a\n---\nbody'), /description/)
  assert.match(checkSkillContent('---\nname:\ndescription: d\n---\nbody'), /name/) // 写了键但值是空的
})

test('name 要符合规范：小写字母、数字和连字符', () => {
  for (const bad of ['Bad Name', 'UPPER', 'a_b', 'a--b', '-a', 'a-', '中文名']) {
    assert.match(checkSkillContent(`---\nname: ${bad}\ndescription: d\n---\n`), /name/, bad)
  }
  for (const good of ['a', 'code-review', 'v2', 'a-b-c']) {
    assert.equal(checkSkillContent(`---\nname: ${good}\ndescription: d\n---\n`), null, good)
  }
})

test('name 不能超过 64 个字符', () => {
  assert.match(checkSkillContent(`---\nname: ${'a'.repeat(65)}\ndescription: d\n---\n`), /64/)
  assert.equal(checkSkillContent(`---\nname: ${'a'.repeat(64)}\ndescription: d\n---\n`), null)
})

test('空内容也算没有 frontmatter', () => {
  assert.match(checkSkillContent(''), /frontmatter/)
})

test('折叠块写法的 description（> 或 |）也认', () => {
  assert.equal(checkSkillContent('---\nname: a\ndescription: >\n  第一行\n  第二行\n---\nbody'), null)
  assert.equal(checkSkillContent('---\nname: a\ndescription: |\n  第一行\n---\nbody'), null)
})

test('读取文件开头只需要一小段：frontmatter 一般在最前面，不用把大文件整个读进内存', async () => {
  const { readHead, HEAD_BYTES } = await import('./plugins.js')
  assert.ok(HEAD_BYTES >= 2048 && HEAD_BYTES <= 64 * 1024)
  const slices = []
  const file = { slice: (a, b) => (slices.push([a, b]), { text: async () => 'head-text' }) }
  assert.equal(await readHead(file), 'head-text')
  assert.deepEqual(slices, [[0, HEAD_BYTES]])
})
