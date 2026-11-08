"""业务层：视图只管 HTTP，真正的逻辑放这里。"""

import logging
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.db.models import Avg

from skills import load_skill_file, parse_frontmatter  # core：能不能当 skill，以它的判断为准

from . import harness
from .models import ChatSession, Eureka, FileChange, Message, SkillSelection
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


def used_plugin_names(session: ChatSession) -> list[str]:
    """这个会话里正在用的插件的 frontmatter name：由 skill_selector 选中、已经把全文放进对话的那些。

    全文以 <skill name="x"> 块的形式一直留在对话里（core 的 skills.skills_message 生成），所以只要对话里有，
    后面每一轮都算在用。没有「勾选」这回事：用不用插件，全由模型在每句话之前选。
    """
    seen = "\n".join(str(m.get("content")) for m in session.conversation if m.get("role") == "user")
    return list(dict.fromkeys(re.findall(r'<skill name="([^"]+)">', seen)))


def save_assistant_message(session: ChatSession, reply: str) -> Message:
    """存下模型的回复；如果这一轮是 eureka 时刻，同时存一条 Eureka（单独的表，不进上下文）。

    判定出任何问题都只是「没有 eureka」，绝不影响回复本身。
    """
    message = Message.objects.create(session=session, role="assistant", content=reply)
    try:
        from . import workflows  # 放在函数里：workflows 要用本文件的 PluginError 等，顶层互相导入会循环

        hit = workflows.match_eureka(session, used_plugin_names(session), reply)
        if hit:
            eureka = Eureka.objects.create(
                message=message, plugin=hit["plugin"], workflow=hit["workflow"], text=workflows.eureka_text()
            )
            try:
                from . import workflow_runtime

                # 展开工作流：只创建运行记录并入队就返回，不在聊天请求里执行；失败只是没有工作流，不影响提示和回复
                workflow_runtime.start_run(eureka, hit["payload"])
            except Exception:
                logging.getLogger(__name__).exception("展开 eureka 工作流失败，已忽略")
    except Exception:
        logging.getLogger(__name__).exception("eureka 判定失败，已忽略")
    try:
        detect_file_change(session, message)
    except Exception:
        logging.getLogger(__name__).exception("文件变更检测失败，已忽略")
    try:
        save_skill_selection(session, message)
    except Exception:
        logging.getLogger(__name__).exception("选中 skill 提示保存失败，已忽略")
    return Message.objects.get(pk=message.pk)  # 重新取一次：让 message.eureka 能读到刚存的那条


DEFAULT_SKILL_SELECTION_TEXT = "🧩 已为你选用插件：{plugins}"
DEFAULT_SKILL_SELECTION_WORKFLOW_TEXT = "🧩 已为你选用插件：{plugins}，并启用工作流：{workflows} 🚀"


def _joined(names: list[str]) -> str:
    """名字用「和」连起来：a / a 和 b / a、b 和 c。"""
    return names[0] if len(names) == 1 else "、".join(names[:-1]) + " 和 " + names[-1]


def save_skill_selection(session: ChatSession, message: Message) -> SkillSelection | None:
    """skill_selector 这一轮选中了 skill，就存一条 SkillSelection 挂在 message 上；工作流是其中有同名工作流的那些。

    选中的名字由 harness._finish 记在 session.selected_skills 上；这里取走并清掉，免得带到下一轮。
    判定出任何问题都只是「没有提示」，调用方会兜住异常，不影响回复本身。
    """
    plugins = list(getattr(session, "selected_skills", None) or [])
    session.selected_skills = []
    if not plugins:
        return None
    from . import workflows  # 放在函数里：workflows 要用本文件的 PluginError 等，顶层互相导入会循环

    flows = [name for name in plugins if workflows.workflow_for(session, name)]
    template = DEFAULT_SKILL_SELECTION_WORKFLOW_TEXT if flows else DEFAULT_SKILL_SELECTION_TEXT
    text = template.format(plugins=_joined(plugins), workflows=_joined(flows) if flows else "")
    return SkillSelection.objects.create(message=message, text=text, plugins=plugins, workflows=flows)


DEFAULT_FILE_CHANGE_TEXT = "📝 有文件变更"


def _workspace_snapshot(session: ChatSession) -> dict:
    """工作区里所有文件的快照 {相对路径: [mtime_ns, size]}；工作区还不存在就是空。"""
    root = Path(harness.workspace_dir(session))
    snapshot = {}
    for path in root.rglob("*"):
        if path.is_file():
            stat = path.stat()
            snapshot[path.relative_to(root).as_posix()] = [stat.st_mtime_ns, stat.st_size]
    return snapshot


def detect_file_change(session: ChatSession, message: Message) -> FileChange | None:
    """和上一次的快照比较：工作区有文件创建或变更，就存一条 FileChange 挂在 message 上；然后更新快照。

    判定出任何问题都只是「没有提示」，调用方会兜住异常，不影响回复本身。
    """
    old, new = session.file_snapshot or {}, _workspace_snapshot(session)
    files = [
        {"path": path, "kind": "created" if path not in old else "modified"}
        for path in sorted(new)
        if old.get(path) != new[path]
    ]
    session.file_snapshot = new
    session.save(update_fields=["file_snapshot"])
    if not files:
        return None
    text = settings.FILE_CHANGE_TEXT.strip() or DEFAULT_FILE_CHANGE_TEXT
    return FileChange.objects.create(message=message, text=text, files=files)


MAX_VIEW_BYTES = 200 * 1024  # 查看文件内容最多读这么多，防止大文件撑爆页面


def list_workspace_files(session: ChatSession) -> list[dict]:
    """本会话工作区里所有文件 [{"path", "size"}]，按路径排序；路径固定是 harness.workspace_dir(session)。"""
    snapshot = _workspace_snapshot(session)
    return [{"path": path, "size": meta[1]} for path, meta in sorted(snapshot.items())]


def read_workspace_file(session: ChatSession, rel_path: str) -> dict | None:
    """读工作区里一个文件的文本内容 {"path", "content", "truncated"}；不存在或跑出工作区就是 None。"""
    root = Path(harness.workspace_dir(session)).resolve()
    path = (root / rel_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():  # 挡住 ../ 和绝对路径
        return None
    with open(path, "rb") as f:
        data = f.read(MAX_VIEW_BYTES + 1)
    return {
        "path": path.relative_to(root).as_posix(),
        "content": data[:MAX_VIEW_BYTES].decode("utf-8", errors="replace"),
        "truncated": len(data) > MAX_VIEW_BYTES,
    }


# 能在浏览器里直接查看的图片、视频和音频格式（后缀 → Content-Type）；不依赖系统的 mimetypes 注册表
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".avif": "image/avif",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
}


def workspace_media(session: ChatSession, rel_path: str) -> tuple[Path, str] | None:
    """工作区里一个图片/视频文件 → (绝对路径, Content-Type)；不存在、跑出工作区或不是媒体格式就是 None。"""
    content_type = MEDIA_TYPES.get(Path(rel_path).suffix.lower())
    root = Path(harness.workspace_dir(session)).resolve()
    path = (root / rel_path).resolve()
    if content_type is None or not path.is_relative_to(root) or not path.is_file():
        return None
    return path, content_type


def write_workspace_file(session: ChatSession, rel_path: str, content: str) -> bool:
    """覆盖工作区里一个已有文件的文本内容；不存在或跑出工作区返回 False。内容超过 MAX_VIEW_BYTES 抛 ValueError。

    同时更新变更快照：这是用户自己改的，不该被当成模型这一轮造成的「文件变更」。
    """
    root = Path(harness.workspace_dir(session)).resolve()
    path = (root / rel_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return False
    data = content.encode("utf-8")
    if len(data) > MAX_VIEW_BYTES:
        raise ValueError("file too large")
    path.write_bytes(data)
    stat = path.stat()
    snapshot = dict(session.file_snapshot or {})
    snapshot[path.relative_to(root).as_posix()] = [stat.st_mtime_ns, stat.st_size]
    session.file_snapshot = snapshot
    session.save(update_fields=["file_snapshot"])
    return True


def send_user_message(
    session: ChatSession, user_text: str, model: str | None = None, effort: str | None = None
) -> list[Message]:
    """存下用户消息，调用 generate_reply，再存下回复；返回这两条消息。第一句话同时成为会话标题。"""
    name_session_from_first_message(session, user_text)
    user_message = Message.objects.create(session=session, role="user", content=user_text)
    # 通过模块属性调用，测试里才能替换 generate_reply
    reply = generate_reply(session, user_text, model, effort)
    assistant_message = save_assistant_message(session, reply)
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
    yield "done", MessageSerializer(save_assistant_message(session, reply)).data


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
#
# 上传的文件要求：文件名以 .md 结尾，并且开头有 frontmatter（--- 包起来的 name 和 description）——
# core 的 skill_selector 要读它的 name / description 做目录，被选中的才把全文放进对话。文件本身原样保存，一个字节不改。
# 没有数据库表：目录就是数据，手动增删文件也立刻生效。
#
# 另外还有「默认插件」：config.ini 的 [storage] default_skills_dir 指向的目录，里面的 .md 对所有会话可见，
# 不能删除（接口返回 403）。它们只读：不会被复制进会话目录，也不会被改动；
# 同名的上传会被拒绝（409），默认插件始终只有一份。没有「勾选」：用哪个插件由 skill_selector 在每句话之前选。

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


def _plugin(name: str, default: bool = False) -> dict:
    return {"name": name, "default": default}


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


def _skill_name(path: Path | None) -> str | None:
    """文件 frontmatter 里的 name；读不了或没有就是 None。"""
    try:
        fm = parse_frontmatter(path.read_bytes().decode("utf-8", errors="replace"))
    except (OSError, AttributeError):
        return None
    return (fm or {}).get("name", "").strip() or None


def list_plugins(session: ChatSession) -> list[dict]:
    """列出这个会话能看到的插件：先是默认插件（所有会话都有），再是会话自己的；各自按名称排序。"""
    defaults = [_plugin(n, default=True) for n in _default_names()]
    own = [_plugin(n) for n in _md_names(_root(session))]
    # skill_name：frontmatter 的 name，skill_selector 选中的是它；前端靠它认出被选中的那一行
    for item in defaults:
        item["skill_name"] = _skill_name(_default_path(item["name"]))
    for item in own:
        item["skill_name"] = _skill_name(_root(session) / item["name"])
    return defaults + own


def get_plugin(session: ChatSession, name: str) -> dict:
    """详情：多一个 content（文件全文）。文件不是 UTF-8 时解不了的字节用 � 代替，不会崩。"""
    default = _default_path(name)
    if default is not None:
        return {**_plugin(default.name, default=True), "content": default.read_bytes().decode("utf-8", errors="replace")}
    path = _plugin_path(session, name)
    if not path.is_file():
        raise PluginError(f"找不到插件 {name}", status=404)
    return {**_plugin(name), "content": path.read_bytes().decode("utf-8", errors="replace")}


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
    """上传一个 .md 文件：原样存成 skills/<文件名>。大小上限和文件上传共用（config.ini 的 max_upload_mb）。"""
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
    return _plugin(name)


def update_plugin(session: ChatSession, name: str, content: str) -> dict:
    """编辑：用新内容整个覆盖已有的会话插件。默认插件只读（403），不存在 404，内容不合格 400（文件不变）。"""
    if _default_path(name) is not None:
        raise PluginError(f"{name} 是默认插件，不能修改", status=403)
    path = _plugin_path(session, name)
    if not path.is_file():
        raise PluginError(f"找不到插件 {name}", status=404)
    raw = content.encode("utf-8")
    if len(raw) > settings.HARNESS_MAX_UPLOAD_BYTES:
        raise PluginError("内容太大", status=413)
    _check_frontmatter(raw)
    path.write_bytes(raw)
    return get_plugin(session, name)


def delete_plugin(session: ChatSession, name: str) -> None:
    if _default_path(name) is not None:
        raise PluginError(f"{name} 是默认插件，不能删除", status=403)
    path = _plugin_path(session, name)
    if not path.is_file():
        raise PluginError(f"找不到插件 {name}", status=404)
    path.unlink()


def all_plugin_paths(session: ChatSession) -> list[str]:
    """这个会话能用的全部插件的文件路径（不管勾没勾）：默认插件在前、会话自己的在后。交给 core 做「先选后载」的候选。"""
    paths = [str(_default_root() / n) for n in _default_names()]
    paths += [str(_root(session) / n) for n in _md_names(_root(session))]
    return paths


def session_stats(session: ChatSession) -> dict:
    """会话统计：请求和工具记录的平均值（没有记录的项是 None）。"""
    r = session.request_stats.aggregate(
        seconds=Avg("seconds"), input_tokens=Avg("input_tokens"), output_tokens=Avg("output_tokens")
    )
    t = session.tool_stats.aggregate(
        seconds=Avg("seconds"), files_read=Avg("files_read"), files_written=Avg("files_written")
    )
    n = session.turn_stats.aggregate(seconds=Avg("seconds"), requests=Avg("requests"), tools=Avg("tools"))
    return {
        "turn": {"avg_seconds": n["seconds"], "avg_requests": n["requests"], "avg_tools": n["tools"]},
        "request": {
            "avg_seconds": r["seconds"],
            "avg_input_tokens": r["input_tokens"],
            "avg_output_tokens": r["output_tokens"],
        },
        "tool": {
            "avg_seconds": t["seconds"],
            "avg_files_read": t["files_read"],
            "avg_files_written": t["files_written"],
        },
    }


STATS_DETAIL_LIMIT = 200  # 明细每类最多返回最近多少条


def session_stats_detail(session: ChatSession) -> dict:
    """会话统计的明细：每个轮一行（最近的在前），行里带着这一轮的请求和工具记录，保存了什么字段就返回什么。"""
    turns = session.turn_stats.order_by("-id").prefetch_related("request_stats", "tool_stats")[:STATS_DETAIL_LIMIT]
    return {
        "turns": [
            {
                "id": t.id,
                "seconds": t.seconds,
                "requests": t.requests,
                "tools": t.tools,
                "request_list": [
                    {"id": r.id, "seconds": r.seconds, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens}
                    for r in t.request_stats.all()
                ],
                "tool_list": [
                    {"id": x.id, "name": x.name, "seconds": x.seconds, "files_read": x.files_read, "files_written": x.files_written, "paths": x.paths}
                    for x in t.tool_stats.all()
                ],
            }
            for t in turns
        ]
    }


def request_stat_content(session: ChatSession, request_id: int) -> dict | None:
    """一次请求发给模型的内容和模型的输出（JSON）。不属于这个会话或不存在就是 None。"""
    stat = session.request_stats.filter(pk=request_id).first()
    return {"input": stat.input_messages, "output": stat.output_messages} if stat else None
