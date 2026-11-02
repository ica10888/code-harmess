"""控制层（C）：HTTP 接口。身份来自 session cookie，只能访问自己创建的会话。"""

import json
import time

from django.http import Http404, StreamingHttpResponse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import ChatSession, Message
from .serializers import ChatSessionSerializer, MessageSerializer, SendMessageSerializer


def get_own_session(request, session_id) -> ChatSession:
    """取当前浏览器自己的会话；不存在或不是自己的，都当 404（不暴露别人的会话是否存在）。"""
    try:
        return ChatSession.objects.get(pk=session_id, owner_key=services.owner_key_of(request))
    except (ChatSession.DoesNotExist, ValueError):
        raise Http404


class SessionListView(APIView):
    def get(self, request):
        sessions = ChatSession.objects.filter(owner_key=services.owner_key_of(request))
        return Response(ChatSessionSerializer(sessions, many=True).data)

    def post(self, request):
        serializer = ChatSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(owner_key=services.owner_key_of(request))
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MessageListView(APIView):
    def get(self, request, session_id):
        session = get_own_session(request, session_id)
        messages = session.messages.filter(id__gt=int(request.query_params.get("after", 0)))
        return Response(MessageSerializer(messages, many=True).data)

    def post(self, request, session_id):
        session = get_own_session(request, session_id)
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        messages = services.send_user_message(session, serializer.validated_data["content"])
        return Response(MessageSerializer(messages, many=True).data, status=status.HTTP_201_CREATED)


def sse_frame(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class StreamMessageView(APIView):
    """发一句话，回复边生成边推回来（SSE 格式的 POST 响应）。事件见 services.stream_user_message。"""

    def post(self, request, session_id):
        session = get_own_session(request, session_id)
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        events = services.stream_user_message(session, serializer.validated_data["content"])
        response = StreamingHttpResponse((sse_frame(e, d) for e, d in events), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


def iter_events(session, last_id, poll_seconds=1.0, max_loops=None):
    """SSE 帧生成器：每隔 poll_seconds 查一次库，有新消息就发，没有就发心跳（保持连接）。

    max_loops=None 表示一直循环（直到客户端断开）；测试里传个有限的数字。
    """
    loops = 0
    while max_loops is None or loops < max_loops:
        loops += 1
        sent = False
        for message in session.messages.filter(id__gt=last_id):
            last_id = message.id
            sent = True
            payload = json.dumps(MessageSerializer(message).data, ensure_ascii=False)
            yield f"id: {message.id}\ndata: {payload}\n\n"
        if not sent:
            yield ": keepalive\n\n"
        time.sleep(poll_seconds)


class StreamView(APIView):
    def get(self, request, session_id):
        session = get_own_session(request, session_id)
        # 浏览器断线重连时会自动带上 Last-Event-ID；首次连接用 ?after=
        last_id = int(request.headers.get("Last-Event-ID") or request.query_params.get("after") or 0)
        response = StreamingHttpResponse(iter_events(session, last_id), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
