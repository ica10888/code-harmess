// 插件列表的纯逻辑：勾选状态、列表增删替换、上传前检查、错误文案。方便测试，不碰 DOM。
// 插件用「名称」标识：它就是后端存放这个插件的目录名，在一个会话里唯一。

// 插件文件的大小上限和普通文件上传共用，和后端 config.ini 的 [storage] max_upload_mb 保持一致；
// 这里先拦一道，省得白传一个大文件才被拒绝，真正的限制在后端。
const MAX_UPLOAD_MB = 20

/** 已勾选的插件名称，按列表顺序。 */
export const checkedPlugins = (plugins) => plugins.filter((p) => p.enabled).map((p) => p.name)

/** 用服务器返回的新状态替换列表里同名的一项；返回新数组，不改原数组。 */
export const replaceByName = (plugins, updated) => plugins.map((p) => (p.name === updated.name ? updated : p))

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
