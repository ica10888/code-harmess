"""业务层：视图只管 HTTP，真正的逻辑放这里。"""

from pathlib import Path

from django.conf import settings

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


def send_user_message(
    session: ChatSession, user_text: str, model: str | None = None, effort: str | None = None
) -> list[Message]:
    """存下用户消息，调用 generate_reply，再存下回复；返回这两条消息。"""
    user_message = Message.objects.create(session=session, role="user", content=user_text)
    # 通过模块属性调用，测试里才能替换 generate_reply
    reply = generate_reply(session, user_text, model, effort)
    assistant_message = Message.objects.create(session=session, role="assistant", content=reply)
    return [user_message, assistant_message]


def stream_user_message(session: ChatSession, user_text: str, model: str | None = None, effort: str | None = None):
    """send_user_message 的流式版：依次产出 (事件名, 数据)。

    user    存好的用户消息（前端用它替换自己先显示的那条）
    delta   模型刚吐出的一段文字 {"text": ...}
    done    存好的完整 assistant 消息（前端用它替换正在长出来的气泡）
    调用模型失败时不抛异常：把原因当作回复存下来，照样以 done 结束。
    """
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
    """只保留文件名本身：去掉任何目录部分（/ 和 \ 都算），拒绝 '' '.' '..'。

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
