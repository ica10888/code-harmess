"""业务层：视图只管 HTTP，真正的逻辑放这里。"""

import json
import re
import tempfile
from pathlib import Path

from django.conf import settings

from skills import load_skill_file  # core：能不能当 skill，以它的判断为准

from . import harness
from .models import ChatSession, Message
from .serializers import MessageSerializer


def owner_key_of(request):
    """返回当前浏览器的身份（Django session key）；第一次访问时先创建一个。"""
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def generate_reply(session: ChatSession, user_text: str, model: str | None = None, effort: str | None = None) -> str:
    """根据用户的话生成后端回复：交给 backend/core 的 Harness（见 harness.run_turn）。

    调用模型可能失败（没配 key、网络、额度……）：这时把原因当作回复展示给用户，
    不让接口返回 500；这一轮不会进对话，重发即可。
    """
    try:
        return harness.run_turn(session, user_text, model, effort)
    except Exception as e:
        return f"调用模型失败：{e}"


TITLE_MAX_CHARS = 15


def title_from_text(text: str) -> str:
    """把第一句话变成会话标题：空白压成一个空格，超过 15 个字就截断并加 ...。"""
    text = " ".join(text.split())
    return text[:TITLE_MAX_CHARS] + "..." if len(text) > TITLE_MAX_CHARS else text


def name_session_from_first_message(session: ChatSession, user_text: str) -> str | None:
    """会话里的第一句话到来时，用它给会话命名；返回新标题，不需要改名时返回 None。"""
    title = title_from_text(user_text)
    if not title or session.messages.exists():
        return None
    session.title = title
    session.save(update_fields=["title"])
    return title


def send_user_message(
    session: ChatSession, user_text: str, model: str | None = None, effort: str | None = None
) -> list[Message]:
    """存下用户消息，调用 generate_reply，再存下回复；返回这两条消息。第一句话同时成为会话标题。"""
    name_session_from_first_message(session, user_text)
    user_message = Message.objects.create(session=session, role="user", content=user_text)
    # 通过模块属性调用，测试里才能替换 generate_reply
    reply = generate_reply(session, user_text, model, effort)
    assistant_message = Message.objects.create(session=session, role="assistant", content=reply)
    return [user_message, assistant_message]


def stream_user_message(session: ChatSession, user_text: str, model: str | None = None, effort: str | None = None):
    """send_user_message 的流式版：依次产出 (事件名, 数据)。

    title   会话的第一句话到来时，改好的会话标题 {"title": ...}
    user    存好的用户消息（前端用它替换自己先显示的那条）
    delta   模型刚吐出的一段文字 {"text": ...}
    done    存好的完整 assistant 消息（前端用它替换正在长出来的气泡）
    调用模型失败时不抛异常：把原因当作回复存下来，照样以 done 结束。
    """
    title = name_session_from_first_message(session, user_text)  # 必须在存用户消息之前：靠“还没有消息”判断
    if title:
        yield "title", {"title": title}
    yield "user", MessageSerializer(Message.objects.create(session=session, role="user", content=user_text)).data
    try:
        for kind, data in harness.stream_turn(session, user_text, model, effort):
            if kind == "delta":
                yield "delta", {"text": data}
            else:
                reply = data
    except Exception as e:
        reply = f"调用模型失败：{e}"
    yield "done", MessageSerializer(Message.objects.create(session=session, role="assistant", content=reply)).data


class UploadError(Exception):
    """上传不合格。status 是要返回给前端的 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _safe_file_name(raw: str) -> str:
    """只保留文件名本身：去掉任何目录部分（正斜杠和反斜杠都算），拒绝 '' '.' '..'。

    浏览器一般只传文件名，但请求可以手写，../../x 这种不能让它写到工作区外面。
    """
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if name in ("", ".", ".."):
        raise UploadError("文件名不合法")
    return name


def _free_name(directory: Path, name: str) -> str:
    """重名时在扩展名前加 (1)、(2)……，绝不覆盖已有文件（里面可能有模型写的东西）。"""
    path = Path(name)
    candidate, n = name, 0
    while (directory / candidate).exists():
        n += 1
        candidate = f"{path.stem} ({n}){path.suffix}"
    return candidate


def save_upload(session: ChatSession, uploaded) -> dict:
    """把上传的文件存进这个会话的工作区（和模型的文件工具是同一个目录），返回 {"name", "size"}。"""
    if uploaded.size > settings.HARNESS_MAX_UPLOAD_BYTES:
        raise UploadError(f"文件太大，最大 {settings.HARNESS_MAX_UPLOAD_BYTES // (1024 * 1024)} MB", status=413)
    name = _safe_file_name(uploaded.name or "")
    directory = Path(harness.workspace_dir(session))
    directory.mkdir(parents=True, exist_ok=True)
    name = _free_name(directory, name)
    with open(directory / name, "xb") as f:  # x：万一并发下撞了名，报错而不是覆盖
        for chunk in uploaded.chunks():
            f.write(chunk)
    return {"name": name, "size": uploaded.size}


# --- 插件管理：每个会话一份，任何 .md 文件都是一个插件，全部落在文件上 ------------------------
#
#   <存储路径>/input/<会话id>/skills/
#       <文件名>.md          一个插件：用户上传什么就存什么，一个字节不改；文件名就是插件名
#       state.json           哪些插件被勾选：{"<文件名>": true}
#
# 上传的文件要求：文件名以 .md 结尾，并且开头有 frontmatter（--- 包起来的 name 和 description）——
# 勾选之后 core 要读它的 name / description，把全文注入对话（见 harness.skills_message_for）。文件本身原样保存，一个字节不改。
# 没有数据库表：目录就是数据，手动增删文件也立刻生效。
#
# 另外还有「默认插件」：config.ini 的 [storage] default_skills_dir 指向的目录，里面的 .md 对所有会话可见，
# 不能删除（接口返回 403）。它们只读：不会被复制进会话目录，也不会被改动；
# 「是否勾选」仍然各会话各自记在自己的 state.json 里。同名的上传会被拒绝（409），默认插件始终只有一份。

STATE_FILE = "state.json"
MAX_NAME_LENGTH = 200
# Windows 不能用作文件名的字符；Linux 上能存，但这套代码要能在两边都跑，所以统一拒绝
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
# Windows 的保留设备名（连带扩展名也不行：CON.md 同样打不开）
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])$", re.IGNORECASE)


class PluginError(Exception):
    """插件请求不合格。status 是要返回给前端的 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def plugin_name(raw: str) -> str:
    """上传的文件名 → 插件名：只保留文件名本身，并保证它是一个能安全落到 skills/ 里的 .md 文件名。

    这不是对插件内容的约束，是防止写出 skills/ 以外的位置、或写不出文件：
    去掉任何目录部分（正斜杠和反斜杠都算）；以 .md 结尾；不含 Windows 不能存的字符；不是保留设备名。
    """
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    stem = name[:-3] if name.lower().endswith(".md") else None
    if not stem or not stem.strip(" ."):
        raise PluginError("插件必须是 .md 文件")
    if len(name) > MAX_NAME_LENGTH:
        raise PluginError(f"文件名太长，最多 {MAX_NAME_LENGTH} 个字符")
    if _BAD_CHARS.search(name) or _RESERVED.match(stem.split(".")[0]):
        raise PluginError("文件名里有不能用的字符（不能包含 < > : \" | ? * 或控制字符，也不能是 CON、NUL 等系统保留名）")
    return name


def apply_to_core(action: str, session: ChatSession, plugin: dict) -> None:
    """把插件的变化交给 core。action 是 "created" / "updated" / "deleted"；plugin 是 {"name", "enabled"}。

    现阶段是空方法：这一步只留好位置，不碰 core。
    以后在这里让 core 加载或移除这个插件（它的文件就在 harness.skills_dir(session) 里），
    上面的接口和文件存储都不用动。
    """


def _root(session: ChatSession) -> Path:
    return Path(harness.skills_dir(session))


def _plugin_path(session: ChatSession, name: str) -> Path:
    """插件文件的路径。名字不合格（含 ..、/ 等，或不是 .md）一律当不存在：路径里来的名字不能信。"""
    try:
        clean = plugin_name(name)
    except PluginError:
        raise PluginError(f"找不到插件 {name}", status=404) from None
    if clean != name:  # 带了目录部分的（比如 a/b.md）不是一个合法的插件名，不能悄悄改成别的
        raise PluginError(f"找不到插件 {name}", status=404)
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


def _plugin(name: str, state: dict, default: bool = False) -> dict:
    return {"name": name, "enabled": state.get(name, False), "default": default}


def _md_names(directory: Path) -> list[str]:
    """目录里的 .md 文件名（只算文件，不含子目录和别的杂物），按名称排序；目录不存在就是空。"""
    if not directory.is_dir():
        return []
    return sorted(e.name for e in directory.iterdir() if e.is_file() and e.name.lower().endswith(".md"))


def _default_root() -> Path | None:
    """默认插件目录；没配置就是 None。"""
    return Path(settings.HARNESS_DEFAULT_SKILLS_DIR) if settings.HARNESS_DEFAULT_SKILLS_DIR else None


def _default_names() -> list[str]:
    root = _default_root()
    return _md_names(root) if root else []


def _default_path(name: str) -> Path | None:
    """名称对应的默认插件文件；不是默认插件就是 None。比较时不分大小写（和文件系统一致）。"""
    root = _default_root()
    if root is None:
        return None
    for n in _default_names():
        if n.lower() == name.lower():
            return root / n
    return None


def list_plugins(session: ChatSession) -> list[dict]:
    """列出这个会话能看到的插件：先是默认插件（所有会话都有），再是会话自己的；各自按名称排序。"""
    state = _read_state(session)
    defaults = [_plugin(n, state, default=True) for n in _default_names()]
    own = [_plugin(n, state) for n in _md_names(_root(session))]
    return defaults + own


def get_plugin(session: ChatSession, name: str) -> dict:
    """详情：多一个 content（文件全文）。文件不是 UTF-8 时解不了的字节用 � 代替，不会崩。"""
    state = _read_state(session)
    default = _default_path(name)
    if default is not None:
        return {**_plugin(default.name, state, default=True), "content": default.read_bytes().decode("utf-8", errors="replace")}
    path = _plugin_path(session, name)
    if not path.is_file():
        raise PluginError(f"找不到插件 {name}", status=404)
    return {**_plugin(name, state), "content": path.read_bytes().decode("utf-8", errors="replace")}


def _check_frontmatter(raw: bytes) -> None:
    """上传的内容要能被 core 当 skill 用：开头有 frontmatter，里面有合规的 name 和 description。

    不自己再写一遍解析规则，直接用 core 的 skills.load_skill_file：能不能当 skill，以 core 的判断为准。
    它只认磁盘上的文件，所以先写到临时文件里让它读。
    """
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.md"
        probe.write_bytes(raw)
        try:
            load_skill_file(str(probe))
        except ValueError as e:
            raise PluginError(
                f"插件文件开头必须有 frontmatter，包含 name（小写字母、数字、连字符）和 description：{e}"
            ) from None


def create_plugin(session: ChatSession, uploaded) -> dict:
    """上传一个 .md 文件：原样存成 skills/<文件名>，默认不勾选。大小上限和文件上传共用（config.ini 的 max_upload_mb）。"""
    name = plugin_name(uploaded.name or "")
    if _default_path(name) is not None:  # 默认插件始终只有一份，不允许被同名文件盖住
        raise PluginError(f"已经有叫 {name} 的默认插件，请换一个文件名", status=409)
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
        raise PluginError(f"已经有叫 {name} 的插件，请先删除再上传", status=409) from None
    state = _read_state(session)
    if state.pop(name, None) is not None:  # 以前同名插件留下的勾选状态不能带到新的上面
        _write_state(session, state)
    plugin = _plugin(name, state)
    apply_to_core("created", session, plugin)
    return plugin


def set_plugin_enabled(session: ChatSession, name: str, enabled: bool) -> dict:
    """勾选 / 取消勾选。默认插件也可以勾选，状态记在这个会话自己的 state.json 里（默认目录一个字节都不动）。"""
    default = _default_path(name)
    if default is not None:
        name, is_default = default.name, True
    else:
        if not _plugin_path(session, name).is_file():
            raise PluginError(f"找不到插件 {name}", status=404)
        is_default = False
    state = _read_state(session)
    state[name] = enabled
    _write_state(session, state)
    plugin = _plugin(name, state, default=is_default)
    apply_to_core("updated", session, plugin)
    return plugin


def delete_plugin(session: ChatSession, name: str) -> None:
    if _default_path(name) is not None:
        raise PluginError(f"{name} 是默认插件，不能删除", status=403)
    path = _plugin_path(session, name)
    if not path.is_file():
        raise PluginError(f"找不到插件 {name}", status=404)
    state = _read_state(session)
    plugin = _plugin(name, state)
    path.unlink()
    if state.pop(name, None) is not None:
        _write_state(session, state)
    apply_to_core("deleted", session, plugin)


def checked_plugin_paths(session: ChatSession) -> list[str]:
    """这个会话里已勾选的插件的文件路径：默认插件在前、会话自己的在后，各自按名称排序。

    文件已经不在了的（勾选后被手动删掉）直接跳过，不报错。注入给 core 用。
    """
    state = _read_state(session)
    paths = []
    for name in _default_names():
        if state.get(name):
            paths.append(str(_default_root() / name))
    for name in _md_names(_root(session)):
        if state.get(name):
            paths.append(str(_root(session) / name))
    return paths
