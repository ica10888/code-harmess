// 插件列表的纯逻辑：列表删除、上传前检查、错误文案。方便测试，不碰 DOM。
// 插件用「名称」标识：它就是后端存放这个插件的目录名，在一个会话里唯一。

// 插件文件的大小上限和普通文件上传共用，和后端 config.ini 的 [storage] max_upload_mb 保持一致；
// 这里先拦一道，省得白传一个大文件才被拒绝，真正的限制在后端。
const MAX_UPLOAD_MB = 20

/** 删掉指定名称的一项；返回新数组，不改原数组。 */
export const removeByName = (plugins, name) => plugins.filter((p) => p.name !== name)

/**
 * 选好的文件能不能当插件传。返回 null 表示可以，否则是要提示给用户的原因。
 * 唯一的条件是文件名以 .md 结尾（不看大小写）；对内容没有任何要求，空文件也可以。
 */
export function checkSkillFile(file) {
  if (!file) return '请先选择一个 .md 文件'
  if (!/\.md$/i.test(file.name) || file.name.length <= 3) return '插件必须是 .md 文件'
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `文件太大，最大 ${MAX_UPLOAD_MB} MB`
  return null
}

const BY_STATUS = {
  400: '插件必须是 .md 文件',
  403: '默认插件不能删除',
  404: '这个插件不存在，请刷新页面',
  409: '这个会话里已有同名插件，请先删除再上传',
  413: '文件太大',
}

/** axios 抛出的错误 → 给用户看的一句话。后端给了原因就用后端的。 */
export function pluginErrorMessage(err) {
  const res = err?.response
  if (!res) return '网络出错，请检查连接后重试'
  return res.data?.detail || BY_STATUS[res.status] || `操作失败（${res.status}）`
}

// --- 内容检查：skill_selector 要读 frontmatter 里的 name 和 description 做目录 ---
//
// 规则和后端 core 的 skills._load 保持一致；这里只是提前检查、给出明确提示，真正的校验在后端（会返回 400）。
// frontmatter 一般就在文件最前面，所以只读开头一小段，不用把大文件整个读进内存。

export const HEAD_BYTES = 8 * 1024
const NAME_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/ // 小写字母、数字、单个连字符

/** 读文件开头一小段文字（frontmatter 就在这里面）。 */
export const readHead = (file) => file.slice(0, HEAD_BYTES).text()

const EXAMPLE = '文件开头需要这样的 frontmatter：\n---\nname: my-skill\ndescription: 这个插件是做什么的、什么时候用\n---'

/** 取 frontmatter 里的顶层键值（只认 core 需要的写法：单行、引号、> 和 | 块）。没有 frontmatter 返回 null。 */
function parseFrontmatter(text) {
  const lines = text.replace(/^﻿/, '').split(/\r?\n/)
  if (lines[0]?.trim() !== '---') return null
  const end = lines.findIndex((line, i) => i > 0 && line.trim() === '---')
  if (end < 0) return { unterminated: true }
  const fields = {}
  const body = lines.slice(1, end)
  for (let i = 0; i < body.length; i++) {
    const m = /^([A-Za-z][\w-]*):(.*)$/.exec(body[i])
    if (!m) continue
    let value = m[2].trim()
    const continuation = []
    while (i + 1 < body.length && (!body[i + 1].trim() || /^[ \t]/.test(body[i + 1]))) continuation.push(body[++i].trim())
    if (['>', '>-', '>+', '|', '|-', '|+'].includes(value)) value = continuation.filter(Boolean).join(' ')
    else if (value) value = [value, ...continuation.filter(Boolean)].join(' ').replace(/^(['"])(.*)\1$/, '$2')
    fields[m[1]] = value
  }
  return fields
}

/** 文件开头的内容能不能当插件用。返回 null 表示可以，否则是要提示给用户的原因。 */
export function checkSkillContent(text) {
  const fm = parseFrontmatter(text)
  if (fm === null) return `插件文件开头必须有 frontmatter（name 和 description）。${EXAMPLE}`
  if (fm.unterminated) return 'frontmatter 没有用 --- 结束，请在 name 和 description 之后再写一行 ---'
  const name = (fm.name ?? '').trim()
  if (!name) return `frontmatter 里缺少 name。${EXAMPLE}`
  if (name.length > 64) return 'name 不能超过 64 个字符'
  if (!NAME_RE.test(name)) return 'name 只能用小写字母、数字和连字符（如 code-review），不能有空格、大写、下划线或中文'
  if (!(fm.description ?? '').trim()) return `frontmatter 里缺少 description。${EXAMPLE}`
  return null
}
