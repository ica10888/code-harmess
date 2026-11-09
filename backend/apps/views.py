"""控制层（C）：HTTP 接口。身份来自 session cookie，只能访问自己创建的会话。"""

import json
import time

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Exists, OuterRef
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from providers import DEFAULT_EFFORT  # core：默认推理强度的唯一来源

from . import services, workflow_runtime, workflows
from .models import ChatSession, StageRun, TurnStat, WorkflowRun
from .options import Effort, Model
from .serializers import (
    ChatSessionSerializer,
    MessageSerializer,
    PluginDetailSerializer,
    PluginSerializer,
    EurekaRunSerializer,
    SendMessageSerializer,
    WebhookLogSerializer,
)


def get_own_session(request, session_id) -> ChatSession:
    """取当前浏览器自己的会话；不存在或不是自己的，都当 404（不暴露别人的会话是否存在）。"""
    try:
        return ChatSession.objects.get(pk=session_id, owner_key=services.owner_key_of(request))
    except (ChatSession.DoesNotExist, ValueError):
        raise Http404


class SessionListView(APIView):
    def get(self, request):
        sessions = ChatSession.objects.filter(owner_key=services.owner_key_of(request)).annotate(
            running=Exists(TurnStat.objects.filter(session=OuterRef("pk"), finished=False))
        )
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
            return Response(services.save_upload(session, uploaded, str(request.data.get("dir", ""))), status=status.HTTP_201_CREATED)
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


def text_upload(data):
    """JSON 里的 name + content → 一个和上传文件等价的对象；缺字段就是 None。"""
    name, content = data.get("name"), data.get("content")
    if not isinstance(name, str) or not isinstance(content, str):
        return None
    return SimpleUploadedFile(name, content.encode("utf-8"))


def plugin_error(e: services.PluginError) -> Response:
    return Response({"detail": str(e)}, status=e.status)


class WorkspaceFileListView(APIView):
    """本会话工作区的文件列表。"""

    def get(self, request, session_id):
        session = get_own_session(request, session_id)
        return Response(services.list_workspace_files(session))


class WorkspaceFileContentView(APIView):
    """查看本会话工作区里一个文件的内容：?path=<相对路径>。"""

    def get(self, request, session_id):
        session = get_own_session(request, session_id)
        result = services.read_workspace_file(session, request.query_params.get("path", ""))
        if result is None:
            raise Http404
        return Response(result)

    def put(self, request, session_id):
        """编辑：body {path, content}，覆盖已有文件；不存在 404，太大 413。"""
        session = get_own_session(request, session_id)
        content = request.data.get("content")
        if not isinstance(content, str):
            return Response({"detail": "content 必须是字符串"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ok = services.write_workspace_file(session, str(request.data.get("path", "")), content)
        except ValueError:
            return Response({"detail": "文件太大"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        if not ok:
            raise Http404
        return Response({"path": request.data["path"]})


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """解析 `bytes=a-b` / `bytes=a-` / `bytes=-n`，返回闭区间 (start, end)；格式不对或越界返回 None。"""
    try:
        unit, _, spec = header.partition("=")
        first, _, last = spec.strip().partition("-")
        if unit.strip() != "bytes" or "," in spec:
            return None
        if first == "":  # 最后 n 个字节
            start, end = max(size - int(last), 0), size - 1
        else:
            start, end = int(first), int(last) if last else size - 1
    except ValueError:
        return None
    end = min(end, size - 1)
    return (start, end) if 0 <= start <= end else None


def iter_file(path, start: int, end: int, chunk: int = 64 * 1024):
    with open(path, "rb") as f:
        f.seek(start)
        left = end - start + 1
        while left > 0 and (data := f.read(min(chunk, left))):
            left -= len(data)
            yield data


class WorkspaceMediaView(APIView):
    """查看本会话工作区里的图片/视频原文件：?path=<相对路径>。支持 Range，视频才能拖动进度条。"""

    def get(self, request, session_id):
        session = get_own_session(request, session_id)
        media = services.workspace_media(session, request.query_params.get("path", ""))
        if media is None:
            raise Http404
        path, content_type = media
        size = path.stat().st_size
        header = request.headers.get("Range")
        byte_range = parse_range(header, size) if header else None
        if header and byte_range is None:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
        else:
            start, end = byte_range or (0, size - 1)
            response = StreamingHttpResponse(iter_file(path, start, end), content_type=content_type, status=206 if byte_range else 200)
            response["Content-Length"] = str(end - start + 1) if size else "0"
            if byte_range:
                response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Accept-Ranges"] = "bytes"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "sandbox"  # SVG 被直接打开时也不能执行脚本
        return response


class WorkspaceDownloadView(APIView):
    """下载本会话工作区里的一个文件（任意类型）：?path=<相对路径>。"""

    def get(self, request, session_id):
        session = get_own_session(request, session_id)
        path = services.workspace_file_path(session, request.query_params.get("path", ""))
        if path is None:
            raise Http404
        response = FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)
        response["X-Content-Type-Options"] = "nosniff"
        return response


class PluginListView(APIView):
    """GET 列出这个会话的插件；POST 上传一个 .md 文件新增插件（表单字段 file）。"""

    parser_classes = [MultiPartParser, JSONParser]

    def get(self, request, session_id):
        session = get_own_session(request, session_id)
        return Response(PluginSerializer(services.list_plugins(session), many=True).data)

    def post(self, request, session_id):
        session = get_own_session(request, session_id)
        uploaded = request.FILES.get("file") or text_upload(request.data)  # 也可以在线新建：JSON {name, content}
        if uploaded is None:
            return Response({"detail": "请选择一个 .md 文件（表单字段 file），或提交 name 和 content"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            plugin = services.create_plugin(session, uploaded)
        except services.PluginError as e:
            return plugin_error(e)
        return Response(PluginSerializer(plugin).data, status=status.HTTP_201_CREATED)


class PluginDetailView(APIView):
    """GET 看详情（带文件全文）；DELETE 删除。<name> 就是插件的文件名（如 notes.md）。"""

    def get(self, request, session_id, name):
        session = get_own_session(request, session_id)
        try:
            return Response(PluginDetailSerializer(services.get_plugin(session, name)).data)
        except services.PluginError as e:
            return plugin_error(e)

    def put(self, request, session_id, name):
        session = get_own_session(request, session_id)
        content = request.data.get("content")
        if not isinstance(content, str):
            return Response({"detail": "缺少 content"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            return Response(PluginDetailSerializer(services.update_plugin(session, name, content)).data)
        except services.PluginError as e:
            return plugin_error(e)

    def delete(self, request, session_id, name):
        session = get_own_session(request, session_id)
        try:
            services.delete_plugin(session, name)
        except services.PluginError as e:
            return plugin_error(e)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkflowListView(APIView):
    """GET 列出这个会话的工作流；POST 上传一个 .md 文件新增工作流（表单字段 file）。返回格式和插件一样。"""

    parser_classes = [MultiPartParser, JSONParser]

    def get(self, request, session_id):
        session = get_own_session(request, session_id)
        return Response(PluginSerializer(workflows.list_workflows(session), many=True).data)

    def post(self, request, session_id):
        session = get_own_session(request, session_id)
        uploaded = request.FILES.get("file") or text_upload(request.data)  # 也可以在线新建：JSON {name, content}
        if uploaded is None:
            return Response({"detail": "请选择一个 .md 文件（表单字段 file），或提交 name 和 content"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            workflow = workflows.create_workflow(session, uploaded)
        except services.PluginError as e:
            return plugin_error(e)
        return Response(PluginSerializer(workflow).data, status=status.HTTP_201_CREATED)


class WorkflowDetailView(APIView):
    """GET 看详情（带文件全文）；DELETE 删除。<name> 就是工作流的文件名（如 flow.md）。"""

    def get(self, request, session_id, name):
        session = get_own_session(request, session_id)
        try:
            return Response(PluginDetailSerializer(workflows.get_workflow(session, name)).data)
        except services.PluginError as e:
            return plugin_error(e)

    def put(self, request, session_id, name):
        session = get_own_session(request, session_id)
        content = request.data.get("content")
        if not isinstance(content, str):
            return Response({"detail": "缺少 content"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            return Response(PluginDetailSerializer(workflows.update_workflow(session, name, content)).data)
        except services.PluginError as e:
            return plugin_error(e)

    def delete(self, request, session_id, name):
        session = get_own_session(request, session_id)
        try:
            workflows.delete_workflow(session, name)
        except services.PluginError as e:
            return plugin_error(e)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- eureka 工作流：webhook 接收和进度 ---------------------------------------------------

LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


class WebhookLogView(APIView):
    """插件通过 webhook 回传一行日志。不靠登录（插件线程没有用户的 cookie），靠两道门：
    ① 令牌：每次运行一个，只对这一个 run 有效；② 来源：只允许本机。"""

    authentication_classes: list = []

    def post(self, request, run_id, stage_key):
        from .workflows_run import verify_run_token

        header = request.META.get("HTTP_AUTHORIZATION", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        if request.META.get("REMOTE_ADDR") not in LOOPBACK or not verify_run_token(run_id, token):
            return Response({"detail": "forbidden"}, status=status.HTTP_403_FORBIDDEN)  # 先过门，再谈有没有这个 run
        serializer = WebhookLogSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not StageRun.objects.filter(run_id=run_id, key=stage_key).exists():
            raise Http404
        written = workflow_runtime.write_log_directly(run_id, stage_key, serializer.validated_data["seq"], serializer.validated_data["text"])
        return Response(status=status.HTTP_201_CREATED if written else status.HTTP_200_OK)  # 重发：已经有了，也算成功

    def get(self, request, run_id, stage_key):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)


class EurekaRunView(APIView):
    """某个会话里一次工作流运行的进度：运行状态、每个阶段的状态和日志。只有会话的主人能看。"""

    def get(self, request, session_id, run_id):
        session = get_own_session(request, session_id)
        run = WorkflowRun.objects.filter(pk=run_id, eureka__message__session=session).prefetch_related("stages__logs").first()
        if run is None:
            raise Http404
        return Response(EurekaRunSerializer(run).data)


class SessionStatsView(APIView):
    """会话统计：请求响应时间、token、工具耗时、读写文件数的平均值。"""

    def get(self, request, session_id):
        return Response(services.session_stats(get_own_session(request, session_id)))


class SessionStatsDetailView(APIView):
    """会话统计的明细列表：每个轮、每次请求、每次工具使用保存的字段。"""

    def get(self, request, session_id):
        return Response(services.session_stats_detail(get_own_session(request, session_id)))


class RequestStatContentView(APIView):
    """展开一次请求：发给模型的输入和模型的输出，保持 JSON 格式。"""

    def get(self, request, session_id, request_id):
        content = services.request_stat_content(get_own_session(request, session_id), request_id)
        if content is None:
            raise Http404
        return Response(content)
