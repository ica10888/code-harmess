"""插件注册表：启动时按 config.ini 的 [eureka] plugins 列表加载插件，按工作流名称取。

对应参考仓库 init.go 里「选构造函数」的那一步（它的注释就写着 like Plugins）：
主项目只认 base.EurekaPlugin 这个接口，具体用哪个插件在这里决定。

安全：只导入 backend/eureka/plugins/ 下、配置里点名的包，目录名必须是 Python 标识符（不含点、斜杠）。
绝不 import 用户上传的代码——否则上传一个文件就等于任意代码执行。

容错：一个插件加载失败（目录不存在、导入出错、阶段声明不合法……）只跳过它并记一条警告，
不影响其他插件，也不让整个服务起不来。
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field

from .base import EurekaPlugin, validate_stages

PLUGINS_PACKAGE = "eureka.plugins"
_FOLDER = re.compile(r"[a-z][a-z0-9_]*")


def parse_plugin_list(raw: str) -> list[str]:
    """config.ini 里逗号分隔的目录名 → 列表（去空白、去空项、去重，保持顺序）。目录名不合法就抛 ValueError。"""
    folders: list[str] = []
    for part in (raw or "").split(","):
        name = part.strip()
        if not name:
            continue
        if not _FOLDER.fullmatch(name):
            raise ValueError(f"插件目录名必须是小写字母开头、只含小写字母 / 数字 / 下划线：{name!r}")
        if name not in folders:
            folders.append(name)
    return folders


@dataclass
class Registry:
    _plugins: dict[str, EurekaPlugin] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def get(self, name: str) -> EurekaPlugin | None:
        """按工作流名称（frontmatter 的 name）找插件；没有就是 None。"""
        return self._plugins.get(name)

    def names(self) -> list[str]:
        return list(self._plugins)


def load(folders: list[str]) -> Registry:
    """逐个导入 plugins/<目录>/plugin.py，取出其中的 PLUGIN 类并实例化。每个插件各自容错。"""
    registry = Registry()
    for folder in folders:
        if not _FOLDER.fullmatch(folder):  # 调用方绕过了 parse_plugin_list 时的最后一道关
            raise ValueError(f"插件目录名不合法：{folder!r}")
        try:
            module = importlib.import_module(f"{PLUGINS_PACKAGE}.{folder}.plugin")
        except ModuleNotFoundError as e:
            registry.warnings.append(f"跳过插件 {folder}：找不到 {PLUGINS_PACKAGE}.{folder}.plugin（{e}）")
            continue
        except Exception as e:  # noqa: BLE001  插件代码导入时可能抛任何异常
            registry.warnings.append(f"跳过插件 {folder}：导入出错：{e}")
            continue
        cls = getattr(module, "PLUGIN", None)
        if cls is None:
            registry.warnings.append(f"跳过插件 {folder}：plugin.py 里没有 PLUGIN（指向插件类）")
            continue
        if not (isinstance(cls, type) and issubclass(cls, EurekaPlugin)):
            registry.warnings.append(f"跳过插件 {folder}：PLUGIN 必须是 EurekaPlugin 的子类")
            continue
        try:
            plugin = cls()
            validate_stages(plugin)  # 阶段声明有问题，加载时就发现，不等到运行
        except Exception as e:  # noqa: BLE001
            registry.warnings.append(f"跳过插件 {folder}：{e}")
            continue
        if plugin.name in registry._plugins:
            registry.warnings.append(f"忽略重名插件 {plugin.name}（目录 {folder}），已使用前一个")
            continue
        registry._plugins[plugin.name] = plugin
    return registry
