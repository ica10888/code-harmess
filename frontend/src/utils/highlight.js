// 用 Prism.js 按文件扩展名做语法高亮；认不出的类型按纯文本转义。
// Prism 输出的 HTML 里，源码里的 < > & 已被转义，所以结果可以放心用 v-html。
import Prism from './prismCore.js'
import './prismLanguages.js'

const LANGUAGES = {
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  py: 'python', json: 'json', sh: 'bash', bash: 'bash',
  yml: 'yaml', yaml: 'yaml', md: 'markdown', sql: 'sql',
  java: 'java', go: 'go', rs: 'rust', c: 'c', h: 'c', cpp: 'cpp', hpp: 'cpp',
  ini: 'ini', cfg: 'ini', toml: 'toml', css: 'css',
  html: 'markup', htm: 'markup', xml: 'markup', svg: 'markup', vue: 'markup',
}

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 文件路径 → Prism 语言名；认不出返回 null。 */
export function languageOf(path) {
  const name = path.split('/').pop()
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : ''
  return LANGUAGES[ext] ?? null
}

/** 文件路径 + 内容 → 高亮后的 HTML；没有对应语言时返回转义后的纯文本。 */
export function highlightFile(path, content) {
  const language = languageOf(path)
  return language ? Prism.highlight(content, Prism.languages[language], language) : escapeHtml(content)
}
