// 语言组件读的是全局 Prism，所以必须先挂到 globalThis，再导入语言；单独成一个模块，保证执行顺序。
import Prism from 'prismjs/components/prism-core.js'

globalThis.Prism = Prism
export default Prism
