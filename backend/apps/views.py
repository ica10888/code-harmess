"""控制层（C）：HTTP 接口。身份来自 session cookie，只能访问自己创建的会话。"""

import json
import time

from django.conf import settings
from django.http import Http404, StreamingHttpResponse
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from providers import DEFAULT_EFFORT  # core：默认推理强度的唯一来源

from . import services, workflows
from .models import ChatSession, Message
from .options import Effort, Model
from .serializers import (
    ChatSessionSerializer,
    MessageSerializer,
    PluginDetailSerializer,
    PluginSerializer,
    PluginUpdateSerializer,
    SendMessageSerializer,
)


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
        data = serializer.validated_data
        messages = services.send_user_message(session, data["content"], data.get("model"), data.get("effort"))
        return Response(MessageSerializer(messages, many=True).data, status=status.HTTP_201_CREATED)


def sse_frame(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class StreamMessageView(APIView):
    """发一句话，回复边生成边推回来（SSE 格式的 POST 响应）。事件见 services.stream_user_message。"""

    def post(self, request, session_id):
        session = get_own_session(request, session_id)
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        events = services.stream_user_message(session, data["content"], data.get("model"), data.get("effort"))
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


class FileUploadView(APIView):
    """上传一个文件到这个会话的工作区（<存储路径>/input/<会话id>/file/）。表单字段名 file。"""

    parser_classes = [MultiPartParser]

    def post(self, request, session_id):
        session = get_own_session(request, session_id)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            return Response({"detail": "请选择要上传的文件（表单字段 file）"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            return Response(services.save_upload(session, uploaded), status=status.HTTP_201_CREATED)
        except services.UploadError as e:
            return Response({"detail": str(e)}, status=e.status)


class OptionsView(APIView):
    """可选的模型和推理强度（写死的枚举，见 options.py）。不需要会话，页面一打开就拉一次。"""

    def get(self, request):
        def pack(enum):
            return [{"value": c.value, "label": c.label, "hint": c.hint} for c in enum]

        return Response(
            {
                "models": pack(Model),
                "efforts": pack(Effort),  # 按从弱到强排列，滑块直接用这个顺序
                "default_model": settings.DEEPSEEK_MODEL,
                "default_effort": DEFAULT_EFFORT,
            }
        )


def plugin_error(e: services.PluginError) -> Response:
    return Response({"detail": str(e)}, status=e.status)


class PluginListView(APIView):
    """GET 列出这个会话的插件；POST 上传一个 .md 文件新增插件（表单字段 file）。"""

    parser_classes = [MultiPartParser]

    def get(self, request, session_id):
        session = get_own_session(request, session_id)
        return Response(PluginSerializer(services.list_plugins(session), many=True).data)

    def post(self, request, session_id):
        session = get_own_session(request, session_id)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            return Response({"detail": "请选择一个 .md 文件（表单字段 file）"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            plugin = services.create_plugin(session, uploaded)
        except services.PluginError as e:
            return plugin_error(e)
        return Response(PluginSerializer(plugin).data, status=status.HTTP_201_CREATED)


class PluginDetailView(APIView):
    """GET 看详情（带文件全文）；PATCH 改勾选状态；DELETE 删除。<name> 就是插件的文件名（如 notes.md）。"""

    def get(self, request, session_id, name):
        session = get_own_session(request, session_id)
        try:
            return Response(PluginDetailSerializer(services.get_plugin(session, name)).data)
        except services.PluginError as e:
            return plugin_error(e)

    def patch(self, request, session_id, name):
        session = get_own_session(request, session_id)
        serializer = PluginUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            plugin = services.set_plugin_enabled(session, name, serializer.validated_data["enabled"])
        except services.PluginError as e:
            return plugin_error(e)
        return Response(PluginSerializer(plugin).data)

    def delete(self, request, session_id, name):
        session = get_own_session(request, session_id)
        try:
            services.delete_plugin(session, name)
        except services.PluginError as e:
            return plugin_error(e)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkflowListView(APIView):
    """GET 列出这个会话的工作流；POST 上传一个 .md 文件新增工作流（表单字段 file）。返回格式和插件一样。"""

    parser_classes = [MultiPartParser]

    def get(self, request, session_id):
        session = get_own_session(request, session_id)
        return Response(PluginSerializer(workflows.list_workflows(session), many=True).data)

    def post(self, request, session_id):
        session = get_own_session(request, session_id)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            return Response({"detail": "请选择一个 .md 文件（表单字段 file）"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            workflow = workflows.create_workflow(session, uploaded)
        except services.PluginError as e:
            return plugin_error(e)
        return Response(PluginSerializer(workflow).data, status=status.HTTP_201_CREATED)


class WorkflowDetailView(APIView):
    """GET 看详情（带文件全文）；PATCH 改勾选状态；DELETE 删除。<name> 就是工作流的文件名（如 flow.md）。"""

    def get(self, request, session_id, name):
        session = get_own_session(request, session_id)
        try:
            return Response(PluginDetailSerializer(workflows.get_workflow(session, name)).data)
        except services.PluginError as e:
            return plugin_error(e)

    def patch(self, request, session_id, name):
        session = get_own_session(request, session_id)
        serializer = PluginUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            workflow = workflows.set_workflow_enabled(session, name, serializer.validated_data["enabled"])
        except services.PluginError as e:
            return plugin_error(e)
        return Response(PluginSerializer(workflow).data)

    def delete(self, request, session_id, name):
        session = get_own_session(request, session_id)
        try:
            workflows.delete_workflow(session, name)
        except services.PluginError as e:
            return plugin_error(e)
        return Response(status=status.HTTP_204_NO_CONTENT)
