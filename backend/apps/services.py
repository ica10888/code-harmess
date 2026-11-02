"""业务层：视图只管 HTTP，真正的逻辑放这里。"""

from . import harness
from .models import ChatSession, Message
from .serializers import MessageSerializer


def owner_key_of(request):
    """返回当前浏览器的身份（Django session key）；第一次访问时先创建一个。"""
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def generate_reply(session: ChatSession, user_text: str) -> str:
    """根据用户的话生成后端回复：交给 backend/core 的 Harness（见 harness.run_turn）。

    调用模型可能失败（没配 key、网络、额度……）：这时把原因当作回复展示给用户，
    不让接口返回 500；这一轮不会进对话，重发即可。
    """
    try:
        return harness.run_turn(session, user_text)
    except Exception as e:
        return f"调用模型失败：{e}"


def send_user_message(session: ChatSession, user_text: str) -> list[Message]:
    """存下用户消息，调用 generate_reply，再存下回复；返回这两条消息。"""
    user_message = Message.objects.create(session=session, role="user", content=user_text)
    # 通过模块属性调用，测试里才能替换 generate_reply
    reply = generate_reply(session, user_text)
    assistant_message = Message.objects.create(session=session, role="assistant", content=reply)
    return [user_message, assistant_message]


def stream_user_message(session: ChatSession, user_text: str):
    """send_user_message 的流式版：依次产出 (事件名, 数据)。

    user    存好的用户消息（前端用它替换自己先显示的那条）
    delta   模型刚吐出的一段文字 {"text": ...}
    done    存好的完整 assistant 消息（前端用它替换正在长出来的气泡）
    调用模型失败时不抛异常：把原因当作回复存下来，照样以 done 结束。
    """
    yield "user", MessageSerializer(Message.objects.create(session=session, role="user", content=user_text)).data
    try:
        for kind, data in harness.stream_turn(session, user_text):
            if kind == "delta":
                yield "delta", {"text": data}
            else:
                reply = data
    except Exception as e:
        reply = f"调用模型失败：{e}"
    yield "done", MessageSerializer(Message.objects.create(session=session, role="assistant", content=reply)).data
