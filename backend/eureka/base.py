"""eureka 插件接口：主项目（backend/apps）只认这个文件里的东西，不认具体插件。

参考 multi-tenancy-operator 的做法：主程序只依赖接口（TenancyScheduler / TenancyWatcher），具体实现在编译期选；
本项目对应的是「注册表按 config.ini 选插件」（见 registry.py），并且不动态加载用户上传的代码。

一个插件要实现两类方法：
  ① stages()                 声明这个工作流有几个阶段、顺序是什么，返回给主项目
  ② reconcile_<阶段>(ctx)    每个阶段一个方法，写这个阶段的业务逻辑，返回 StageResult

reconcile 的约定（借鉴 Kubernetes 的 Reconcile 语义）：
  - 幂等：同一阶段重复执行，结果一样、副作用不叠加（写文件用覆盖，不用追加）。失败重跑、重启恢复才安全；
  - 成功返回 StageResult；失败直接抛异常，由 runner 捕获并记录，插件不用自己包 try/except；
  - 过程中用 ctx.log("…") 打印进度（它就是 webhook）；不要自己写数据库；
  - 长循环里检查 ctx.cancelled，超时后尽快退出。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 工作流名：和 workflows.py 里 frontmatter 的 name 规则一致（小写字母、数字、单个连字符）
_PLUGIN_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_STAGE_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
_METHOD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

MAX_LOG_CHARS = 2_000  # 单条日志上限：一行文字不能塞满数据库


@dataclass(frozen=True)
class Stage:
    """工作流的一个阶段。"""

    key: str  # 阶段标识，同一个插件内唯一，如 "extract"
    title: str  # 给人看的名字，如「提取审查结果」
    method: str  # 插件上实现这个阶段的方法名，如 "reconcile_extract"

    def __post_init__(self) -> None:
        if not _STAGE_KEY.fullmatch(self.key or ""):
            raise ValueError(f"stage key 必须是小写字母开头、只含小写字母 / 数字 / 下划线（最长 40）：{self.key!r}")
        if not (self.title or "").strip():
            raise ValueError("stage title 不能为空")
        if not _METHOD_NAME.fullmatch(self.method or ""):
            raise ValueError(f"stage method 必须是一个方法名：{self.method!r}")


@dataclass
class StageResult:
    """一个阶段的结果。output 会存起来，后面的阶段能从 ctx.previous 里读到。"""

    output: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageContext:
    """传给每个 reconcile 的上下文：插件只能通过它和主项目打交道，碰不到 Django 对象。"""

    run_id: int
    session_id: str
    payload: dict  # 触发 eureka 的那份 JSON（已经通过 schema 校验）
    workspace: Path | None  # 这个会话的工作区目录；插件只读写这里
    log: Callable[[str], None]  # webhook：打印一行文字，主项目存下来并推给前端
    previous: dict[str, dict] = field(default_factory=dict)  # 前面已完成阶段的输出：{阶段 key: output}
    _cancelled: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        raw_log = self.log

        def safe_log(text: Any) -> None:
            """规范一行日志再交出去：转成字符串、截断、忽略空行；发送失败只丢这一行，不让阶段失败。"""
            line = str(text)[:MAX_LOG_CHARS]
            if not line.strip():
                return
            try:
                raw_log(line)
            except Exception:  # noqa: BLE001  webhook 发不出去不是阶段的错
                pass

        self.log = safe_log

    @property
    def cancelled(self) -> bool:
        """超时或被取消后为 True；长循环里应该检查它并尽快退出。"""
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


class EurekaPlugin(ABC):
    """eureka 插件基类。name 要和对应工作流 frontmatter 的 name 一致，注册表按它查找。"""

    name: str

    def __init__(self) -> None:
        name = getattr(type(self), "name", None)
        if not isinstance(name, str):
            raise TypeError(f"{type(self).__name__} 必须声明类属性 name（和工作流 frontmatter 的 name 一致）")
        if not _PLUGIN_NAME.fullmatch(name) or len(name) > 64:
            raise ValueError(f"插件 name 必须是小写字母、数字、连字符（最长 64）：{name!r}")

    @abstractmethod
    def stages(self) -> list[Stage]:
        """基本方法①：声明这个工作流有几个阶段，返回给主项目。顺序就是执行顺序。"""


def validate_stages(plugin: EurekaPlugin) -> tuple[Stage, ...]:
    """主项目拿到 plugin.stages() 之后先检查：至少一个阶段、key 不重复、每个阶段的方法真的存在而且能调用。

    这些问题要在「创建运行记录」之前就报出来，而不是等某个阶段跑到一半才发现。
    """
    stages = tuple(plugin.stages())
    if not stages:
        raise ValueError(f"插件 {plugin.name} 至少要有一个阶段")
    seen: set[str] = set()
    for stage in stages:
        if not isinstance(stage, Stage):
            raise ValueError(f"插件 {plugin.name} 的 stages() 必须返回 Stage 对象，拿到的是 {stage!r}")
        if stage.key in seen:
            raise ValueError(f"插件 {plugin.name} 的阶段 key 重复：{stage.key}")
        seen.add(stage.key)
        if stage.method.startswith("_") or stage.method in dir(EurekaPlugin):
            raise ValueError(f"阶段 {stage.key} 不能使用 {stage.method} 作为阶段方法（内部方法或基类方法）")
        if not callable(getattr(plugin, stage.method, None)):
            raise ValueError(f"插件 {plugin.name} 没有实现阶段 {stage.key} 的方法 {stage.method}")
    return stages
