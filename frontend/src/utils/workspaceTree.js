/** 把后端的扁平路径列表（a/b.txt）转成树；目录排在文件前面，同类按名字排序。 */
export function buildTree(files) {
  const root = []
  for (const { path } of files) {
    const parts = path.split('/')
    let level = root
    parts.forEach((name, i) => {
      const isFile = i === parts.length - 1
      let node = level.find((n) => n.label === name && !!n.children === !isFile)
      if (!node) {
        node = { label: name, path: parts.slice(0, i + 1).join('/'), ...(isFile ? {} : { children: [] }) }
        level.push(node)
      }
      level = node.children
    })
  }
  const sort = (nodes) => {
    nodes.sort((a, b) => !!b.children - !!a.children || a.label.localeCompare(b.label))
    nodes.forEach((n) => n.children && sort(n.children))
  }
  sort(root)
  return root
}
