// 输入框 markdown 预览的渲染函数测试：node --test，不需要浏览器。
import assert from 'node:assert/strict'
import test from 'node:test'
import { renderMarkdown } from './markdown.js'

test('常见语法：加粗、斜体、行内代码、标题、列表', () => {
  assert.match(renderMarkdown('**粗**'), /<strong>粗<\/strong>/)
  assert.match(renderMarkdown('*斜*'), /<em>斜<\/em>/)
  assert.match(renderMarkdown('`code`'), /<code>code<\/code>/)
  assert.match(renderMarkdown('# 标题'), /<h1>标题<\/h1>/)
  assert.match(renderMarkdown('- a\n- b'), /<ul>\s*<li>a<\/li>\s*<li>b<\/li>\s*<\/ul>/)
})

test('代码块保留内容，并转义里面的尖括号', () => {
  const html = renderMarkdown('```js\nif (a < b) {}\n```')
  assert.match(html, /<pre><code class="language-js">/)
  assert.match(html, /a &lt; b/)
})

test('单个换行也换行（聊天输入的习惯，不用打两个空格）', () => {
  assert.match(renderMarkdown('第一行\n第二行'), /第一行<br>\s*第二行/)
})

test('原始 HTML 不会被执行：脚本和事件属性都被当成普通文字转义', () => {
  const html = renderMarkdown('<script>alert(1)</script><img src=x onerror=alert(1)>')
  assert.doesNotMatch(html, /<script/i)
  assert.doesNotMatch(html, /<img/i)
  assert.match(html, /&lt;script&gt;/)
})

test('javascript: 链接不会变成可点击的链接', () => {
  const html = renderMarkdown('[点我](javascript:alert(1))')
  assert.doesNotMatch(html, /<a /i)
  assert.doesNotMatch(html, /href="javascript/i)
})

test('正常链接在新标签页打开，并且不泄露来源页', () => {
  const html = renderMarkdown('[文档](https://example.com)')
  assert.match(html, /<a href="https:\/\/example\.com"/)
  assert.match(html, /target="_blank"/)
  assert.match(html, /rel="noopener noreferrer"/)
})

test('空输入和只有空白的输入渲染成空字符串', () => {
  assert.equal(renderMarkdown(''), '')
  assert.equal(renderMarkdown('  \n  '), '')
})

test('没写完的语法不会报错（边输入边渲染，经常是半截的）', () => {
  for (const half of ['**没闭合', '```js\nconst a =', '[文字](', '- ', '> ', '| a | b |\n|---']) {
    assert.doesNotThrow(() => renderMarkdown(half), half)
  }
})
