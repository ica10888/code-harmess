import { test } from 'node:test'
import assert from 'node:assert/strict'
import { highlightFile, languageOf } from './highlight.js'

test('按扩展名认出语言', () => {
  assert.equal(languageOf('a/b.py'), 'python')
  assert.equal(languageOf('x.vue'), 'markup')
  assert.equal(languageOf('Makefile'), null)
  assert.equal(languageOf('a.xyz'), null)
})

test('已知扩展名：Prism 加上 token 标签', () => {
  assert.match(highlightFile('a.py', 'def f(): pass'), /<span class="token keyword">def<\/span>/)
  assert.match(highlightFile('a.json', '{"a": 1}'), /token property/)
})

test('未知扩展名：只转义，不执行标签', () => {
  assert.equal(highlightFile('a.xyz', '<b>&'), '&lt;b&gt;&amp;')
})
