"""工作流管理：规则和插件（services.py 的「插件管理」）完全一样，只是换了目录，并且上传的 frontmatter 要有 name、description、schema。

  <存储路径>/input/<会话id>/workflow/<文件名>.md   一个工作流：原样存放，文件名就是工作流名
  config.ini 的 [storage] default_workflow_dir      默认工作流：所有会话可见、只读、不能删除（403）、同名上传 409

文件名检查、错误类型、目录列举都直接复用 services 里插件的那一份。
"""

import json
import re
from pathlib import Path

from django.conf import settings
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from skills import parse_frontmatter  # core：frontmatter 的解析以它为准

from . import harness
from .models import ChatSession
from .services import PluginError, _md_names, plugin_name

_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")  # 和 skill 的 name 规则一致：小写字母、数字、单个连字符


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


def _workflow(name: str, default: bool = False) -> dict:
    return {"name": name, "default": default}


def list_workflows(session: ChatSession) -> list[dict]:
    """这个会话能看到的工作流：先是默认的，再是会话自己的；各自按名称排序。"""
    defaults = [_workflow(n, default=True) for n in _default_names()]
    return defaults + [_workflow(n) for n in _md_names(_root(session))]


def get_workflow(session: ChatSession, name: str) -> dict:
    """详情：多一个 content（文件全文）。文件不是 UTF-8 时解不了的字节用 � 代替，不会崩。"""
    default = _default_path(name)
    if default is not None:
        return {**_workflow(default.name, default=True), "content": default.read_bytes().decode("utf-8", errors="replace")}
    path = _workflow_path(session, name)
    if not path.is_file():
        raise PluginError(f"找不到工作流 {name}", status=404)
    return {**_workflow(name), "content": path.read_bytes().decode("utf-8", errors="replace")}


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
    """上传一个 .md 文件（校验 frontmatter）：原样存成 workflow/<文件名>。大小上限和文件上传共用（max_upload_mb）。"""
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
    return _workflow(name)


def _file_of(session: ChatSession, filename: str) -> Path:
    """工作流文件名对应的路径（默认的优先）。"""
    default = _default_path(filename)
    return default if default is not None else _root(session) / filename


def _frontmatter_name(path: Path) -> str | None:
    """工作流 frontmatter 的 name；读不了或没有就是 None。"""
    try:
        fm = parse_frontmatter(path.read_bytes().decode("utf-8", errors="replace"))
    except OSError:
        return None
    name = (fm or {}).get("name", "").strip()
    return name or None


def _schema_problem(path: Path) -> str | None:
    """工作流文件不能用的原因（给用户看）；能用就是 None。要有 frontmatter 的 name，schema 要是合法的 JSON Schema。"""
    try:
        fm = parse_frontmatter(path.read_bytes().decode("utf-8", errors="replace"))
    except OSError:
        return "文件读不了"
    if not fm or not fm.get("name", "").strip():
        return "frontmatter 缺少 name"
    try:
        Draft202012Validator.check_schema(json.loads(fm["schema"]))
    except (KeyError, TypeError, ValueError, SchemaError):
        return "frontmatter 里的 schema 不是合法的 JSON Schema"
    return None


def delete_workflow(session: ChatSession, name: str) -> None:
    if _default_path(name) is not None:
        raise PluginError(f"{name} 是默认工作流，不能删除", status=403)
    path = _workflow_path(session, name)
    if not path.is_file():
        raise PluginError(f"找不到工作流 {name}", status=404)
    path.unlink()


def workflow_for(session: ChatSession, skill_name: str) -> dict | None:
    """frontmatter 的 name 等于 skill_name、并且能用的工作流 {"file", "name", "path"}（默认的在前）；没有就是 None。

    工作流跟着 skill_selector 选中的 skill 走：同名就是它的输出格式。schema 坏了的不算能用。
    """
    for filename in [*_default_names(), *_md_names(_root(session))]:
        path = _file_of(session, filename)
        if path.is_file() and _frontmatter_name(path) == skill_name and _schema_problem(path) is None:
            return {"file": filename, "name": skill_name, "path": path}
    return None


def workflow_message(session: ChatSession, skill_names: list[str], already: str) -> str | None:
    """选中的 skill 里有同名工作流的，合成一条要注入的消息：全文 + 输出要求。already 是对话里已有的用户消息文字，已经注入过的不再注入。"""
    blocks = []
    for skill_name in skill_names:
        found = workflow_for(session, skill_name)
        if found is None or f'<workflow name="{skill_name}">' in already:
            continue
        body = found["path"].read_bytes().decode("utf-8", errors="replace")
        blocks.append(f'<workflow name="{skill_name}">\n{body}\n</workflow>')
    if not blocks:
        return None
    return (
        "The workflow(s) below belong to the skill(s) selected for this request. Each frontmatter contains the "
        "JSON Schema of the result, so the schema IS available: do not say it is missing. When the user asks for "
        "what the skill does and gives the input it needs, reply with exactly one JSON value that validates "
        "against that schema, inside a ```json fenced block, with no other explanation. If the input is missing "
        "(for example no code to review), ask the user for it instead of refusing or inventing anything.\n\n"
        + "\n\n".join(blocks)
    )


# --- eureka 时刻 ---------------------------------------------------------------------
#
# 用了插件，并且工作流里有 name 相同的文件，而且模型这一轮的回复通过了那个工作流 frontmatter 里的 schema，
# 就算一个 eureka 时刻。这里只管「判定」；保存在 models.Eureka，展示在 serializers / 前端。
# 判定不要求工作流被勾选：只要名字对得上。

DEFAULT_EUREKA_TEXT = "🎉✨ Eureka 时刻！🚀 这一步已通过工作流校验，成果就绪 💡"
# 代码块围栏：三个以上的反引号或波浪号，后面可以跟语言标记（json、JSON，或者没有），内容一直到一模一样的围栏闭合。
# 结尾围栏必须和开头围栏相同：JSON 字符串里含三个反引号的字样不会把它截断，四个反引号的围栏也认。
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n(.*?)\n?[ \t]*\1[ \t]*$", re.DOTALL | re.MULTILINE)


def eureka_text() -> str:
    """要展示的提示文本：config.ini 的 [eureka] text，没配就用内置的默认文本。"""
    return settings.EUREKA_TEXT.strip() or DEFAULT_EUREKA_TEXT


def _scan_json(text: str):
    """从一段文字里依次取出能解析的 JSON 值（前后可以有说明文字）。"""
    decoder = json.JSONDecoder()
    index = 0
    while True:
        starts = [i for i in (text.find("[", index), text.find("{", index)) if i >= 0]
        if not starts:
            return
        start = min(starts)
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1  # 这个括号不是 JSON 的开头，往后找
            continue
        yield value
        index = end


def _json_candidates(reply: str):
    """从回复里依次取出要校验的 JSON 值。

    规则：回复里有 ``` 包裹（语言标记 json / JSON 都行，没有也行）时，先去掉包裹，只校验包裹里面的内容——
    包裹里的才是正式结果，所以包裹里的内容不合格，不会被包裹外面碰巧出现的一段 JSON 救回来；
    有多个包裹时按出现的顺序一个个来。回复里一个完整的包裹都没有（包括被截断、只有开头围栏），
    才从整段文字里找 JSON。每个值只取一次。
    """
    blocks = [m.group(2) for m in _FENCE.finditer(reply)]
    if blocks:
        for block in blocks:
            text = block.strip()
            try:
                yield json.loads(text)  # 包裹里整个就是一个 JSON：最常见的情况
            except ValueError:
                yield from _scan_json(text)  # 包裹里还夹着说明文字时，从里面找
        return
    yield from _scan_json(reply)


def _workflow_schemas(session: ChatSession) -> dict[str, list[tuple[str, dict]]]:
    """{frontmatter 的 name: [(文件名, schema), ...]}：默认工作流 + 这个会话自己的。

    读不了 / 没有 frontmatter / schema 不是合法的 JSON Schema 的文件直接跳过，一个坏文件不影响其他的。
    """
    found: dict[str, list[tuple[str, dict]]] = {}
    sources = [(_default_root(), _default_names()), (_root(session), _md_names(_root(session)))]
    for root, names in sources:
        for filename in names:
            try:
                fm = parse_frontmatter((root / filename).read_bytes().decode("utf-8", errors="replace"))
                schema = json.loads(fm["schema"])
                Draft202012Validator.check_schema(schema)
                found.setdefault(fm["name"].strip(), []).append((filename, schema))
            except (OSError, TypeError, KeyError, ValueError, SchemaError):
                continue
    return found


def match_eureka(session: ChatSession, plugin_names: list[str], reply: str | None) -> dict | None:
    """这一轮是不是 eureka 时刻。是就返回 {"plugin": 插件名, "workflow": 工作流文件名, "payload": 通过校验的那份 JSON}，否则 None。

    plugin_names：这一轮在用的插件的 frontmatter name。reply：模型这一轮的回复文字。
    回复里取出的 JSON 里只要有一个通过了同名工作流的 schema 就算；有多个候选时，先命中的生效。
    """
    if not plugin_names or not reply:
        return None
    schemas = _workflow_schemas(session)
    for plugin in plugin_names:
        candidates = schemas.get(plugin)
        if not candidates:
            continue
        for value in _json_candidates(reply):
            for filename, schema in candidates:
                if Draft202012Validator(schema).is_valid(value):
                    return {"plugin": plugin, "workflow": filename, "payload": value}
    return None
