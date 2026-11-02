// 把输入框里的 markdown 渲染成 HTML，给「实时预览」用。
//
// 安全：用户输入的内容会变成 HTML 放进页面（v-html），所以 html: false，原始 HTML 一律转义成文字；
// markdown-it 自带的链接检查也会拒绝 javascript: 之类的地址。没有额外引入 DOMPurify，因为源头已经堵住了。
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({
  html: false, // 不允许原始 HTML：<script>、onerror= 之类都当普通文字
  breaks: true, // 单个换行也换行，符合聊天输入的习惯
  linkify: false,
})

// 链接一律新标签页打开，并且不把当前页面的地址泄露给对方
const defaultLinkOpen = md.renderer.rules.link_open || ((tokens, i, options, _env, self) => self.renderToken(tokens, i, options))
md.renderer.rules.link_open = (tokens, i, options, env, self) => {
  tokens[i].attrSet('target', '_blank')
  tokens[i].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, i, options, env, self)
}

/** markdown 文本 → HTML 字符串；空白输入返回 ''。半截的语法（边输入边渲染）不会抛错。 */
export function renderMarkdown(text) {
  return text.trim() ? md.render(text) : ''
}
