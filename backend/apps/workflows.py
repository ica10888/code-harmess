"""工作流管理：规则和插件（services.py 的「插件管理」）完全一样，只是换了目录，并且上传的 frontmatter 要有 name、description、schema。

  <存储路径>/input/<会话id>/workflow/<文件名>.md   一个工作流：原样存放，文件名就是工作流名
  <存储路径>/input/<会话id>/workflow/state.json    哪些工作流被勾选：{"<文件名>": true}
  config.ini 的 [storage] default_workflow_dir      默认工作流：所有会话可见、只读、不能删除（403）、同名上传 409

文件名检查、错误类型、目录列举都直接复用 services 里插件的那一份。
"""

import json
import re
from pathlib import Path

from django.conf import settings
from skills import parse_frontmatter  # core：frontmatter 的解析以它为准

from . import harness
from .models import ChatSession
from .services import PluginError, _md_names, plugin_name

STATE_FILE = "state.json"
_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")  # 和 skill 的 name 规则一致：小写字母、数字、单个连字符


def apply_to_core(action: str, session: ChatSession, workflow: dict) -> None:
    """把工作流的变化交给 core。action 是 "created" / "updated" / "deleted"；workflow 是 {"name", "enabled"}。

    现阶段是空方法：工作流还没有实现，这里只留好位置，不碰 core。
    以后在这里让 core 加载或移除这个工作流（文件在 harness.workflow_dir(session) 里），接口和文件存储都不用动。
    """


def _root(session: ChatSession) -> Path:
    return Path(harness.workflow_dir(session))


def _default_root() -> Path | None:
    return Path(settings.HARNESS_DEFAULT_WORKFLOW_DIR) if settings.HARNESS_DEFAULT_WORKFLOW_DIR else None


def _default_names() -> list[str]:
    root = _default_root()
    return _md_names(root) if root else []


def _default_path(name: str) -> Path | None:
    """名称对应的默认工作流文件；不是默认工作流就是 None。比较时不分大小写（和文件系统一致）。"""
    for n in _default_names():
        if n.lower() == name.lower():
            return _default_root() / n
    return None


def _workflow_path(session: ChatSession, name: str) -> Path:
    """工作流文件的路径。名字不合格（含 ..、/ 等，或不是 .md）一律当不存在：路径里来的名字不能信。"""
    try:
        clean = plugin_name(name)
    except PluginError:
        raise PluginError(f"找不到工作流 {name}", status=404) from None
    if clean != name:
        raise PluginError(f"找不到工作流 {name}", status=404)
    return _root(session) / name


def _read_state(session: ChatSession) -> dict:
    """读勾选状态；文件不存在、内容坏了都当作「全都没勾」，不报错。"""
    try:
        data = json.loads((_root(session) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, bool)} if isinstance(data, dict) else {}


def _write_state(session: ChatSession, state: dict) -> None:
    root = _root(session)
    root.mkdir(parents=True, exist_ok=True)
    (root / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _workflow(name: str, state: dict, default: bool = False) -> dict:
    return {"name": name, "enabled": state.get(name, False), "default": default}


def list_workflows(session: ChatSession) -> list[dict]:
    """这个会话能看到的工作流：先是默认的，再是会话自己的；各自按名称排序。"""
    state = _read_state(session)
    defaults = [_workflow(n, state, default=True) for n in _default_names()]
    return defaults + [_workflow(n, state) for n in _md_names(_root(session))]


def get_workflow(session: ChatSession, name: str) -> dict:
    """详情：多一个 content（文件全文）。文件不是 UTF-8 时解不了的字节用 � 代替，不会崩。"""
    state = _read_state(session)
    default = _default_path(name)
    if default is not None:
        return {**_workflow(default.name, state, default=True), "content": default.read_bytes().decode("utf-8", errors="replace")}
    path = _workflow_path(session, name)
    if not path.is_file():
        raise PluginError(f"找不到工作流 {name}", status=404)
    return {**_workflow(name, state), "content": path.read_bytes().decode("utf-8", errors="replace")}


def _check_frontmatter(raw: bytes) -> None:
    """开头必须有 frontmatter，里面有合规的 name、非空的 description，以及能解析成 JSON 对象的 schema。"""
    fm = parse_frontmatter(raw.decode("utf-8", errors="replace"))
    if fm is None:
        raise PluginError("工作流文件开头必须有 frontmatter（--- 包起来），包含 name、description、schema")
    name = fm.get("name", "").strip()
    if not _NAME.fullmatch(name) or len(name) > 64:
        raise PluginError(f"frontmatter 的 name 必须是小写字母、数字、连字符（最长 64）：{name!r}")
    if not fm.get("description", "").strip():
        raise PluginError("frontmatter 缺少 description")
    try:
        schema = json.loads(fm.get("schema", ""))
    except ValueError as e:
        raise PluginError(f"frontmatter 的 schema 必须是 JSON：{e}") from None
    if not isinstance(schema, dict):
        raise PluginError("frontmatter 的 schema 必须是 JSON 对象")


def create_workflow(session: ChatSession, uploaded) -> dict:
    """上传一个 .md 文件（校验 frontmatter）：原样存成 workflow/<文件名>，默认不勾选。大小上限和文件上传共用（max_upload_mb）。"""
    name = plugin_name(uploaded.name or "")
    if _default_path(name) is not None:
        raise PluginError(f"已经有叫 {name} 的默认工作流，请换一个文件名", status=409)
    if uploaded.size > settings.HARNESS_MAX_UPLOAD_BYTES:
        raise PluginError(f"文件太大，最大 {settings.HARNESS_MAX_UPLOAD_BYTES // (1024 * 1024)} MB", status=413)
    raw = uploaded.read()
    _check_frontmatter(raw)  # 先校验再落盘：不合格的不会在磁盘上留下任何东西
    root = _root(session)
    root.mkdir(parents=True, exist_ok=True)
    try:
        with open(root / name, "xb") as f:  # x：已经有同名文件就报错，而不是覆盖
            f.write(raw)
    except FileExistsError:
        raise PluginError(f"已经有叫 {name} 的工作流，请先删除再上传", status=409) from None
    state = _read_state(session)
    if state.pop(name, None) is not None:  # 以前同名工作流留下的勾选状态不能带到新的上面
        _write_state(session, state)
    workflow = _workflow(name, state)
    apply_to_core("created", session, workflow)
    return workflow


def set_workflow_enabled(session: ChatSession, name: str, enabled: bool) -> dict:
    """勾选 / 取消勾选。默认工作流也可以勾选，状态记在这个会话自己的 state.json 里。"""
    default = _default_path(name)
    if default is not None:
        name, is_default = default.name, True
    else:
        if not _workflow_path(session, name).is_file():
            raise PluginError(f"找不到工作流 {name}", status=404)
        is_default = False
    state = _read_state(session)
    state[name] = enabled
    _write_state(session, state)
    workflow = _workflow(name, state, default=is_default)
    apply_to_core("updated", session, workflow)
    return workflow


def delete_workflow(session: ChatSession, name: str) -> None:
    if _default_path(name) is not None:
        raise PluginError(f"{name} 是默认工作流，不能删除", status=403)
    path = _workflow_path(session, name)
    if not path.is_file():
        raise PluginError(f"找不到工作流 {name}", status=404)
    state = _read_state(session)
    workflow = _workflow(name, state)
    path.unlink()
    if state.pop(name, None) is not None:
        _write_state(session, state)
    apply_to_core("deleted", session, workflow)
