"""数据层（M）：会话和消息。"""

import uuid

from django.db import models


class ChatSession(models.Model):
    """一次会话。owner_key 是创建它的浏览器的 Django session key，用来区分用户（不需要登录）。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_key = models.CharField(max_length=64, db_index=True)
    title = models.CharField(max_length=100, default="新会话")
    # 发给模型的完整对话（含工具调用和结果），格式是 core 的 provider 用的；和给用户看的 Message 是两份
    conversation = models.JSONField(default=list, blank=True)
    # 工作区上一次检测时的快照 {相对路径: [mtime_ns, size]}；和这次的快照比较，就知道有文件创建或变更
    file_snapshot = models.JSONField(default=dict, blank=True)
    # 计数（core 的 Agent.counter 累加的结果）：请求 Responses API 的次数、turn 的次数、工具使用次数
    request_count = models.PositiveIntegerField(default=0)
    turn_count = models.PositiveIntegerField(default=0)
    tool_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Message(models.Model):
    """会话里的一条消息。自增 id 同时是 SSE 的游标：前端只要记住最后收到的 id。"""

    ROLE_CHOICES = [("user", "用户"), ("assistant", "后端")]

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content = models.TextField()
    notice = models.TextField(blank=True, default="")  # 给用户看的提示（例如回复没按工作流格式）；不是消息内容，不进模型上下文
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class FileChange(models.Model):
    """文件变更提示：这一轮结束时工作区里有文件创建或变更，就挂在那条 assistant 消息上的一条提示（和 Eureka 同一套逻辑）。

    单独一张表，不进模型上下文。text 存当时展示的文本；files 是 [{"path": ..., "kind": "created"/"modified"}, ...]。
    """

    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="file_change")  # 一条消息最多一个
    text = models.TextField()
    files = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class SkillSelection(models.Model):
    """选中 skill 的提示：skill_selector 这一轮选中了 skill，就挂在那条 assistant 消息上的一条提示（和 FileChange 同一套逻辑）。

    单独一张表，不进模型上下文。text 存当时展示的文本；plugins 是选中的 skill 名，workflows 是其中有同名工作流的那些。
    """

    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="skill_selection")  # 一条消息最多一个
    text = models.TextField()
    plugins = models.JSONField(default=list)
    workflows = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class Eureka(models.Model):
    """eureka 时刻：用了插件、且这条回复通过了同名工作流的 schema 时，挂在那条 assistant 消息上的一条提示。

    单独一张表，不进模型上下文（上下文只在 ChatSession.conversation 里）。text 存的是当时展示的文本：
    以后改了 config.ini 里的文案，老记录不变。plugin / workflow 记下是哪一对命中的，给以后的实现用。
    """

    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="eureka")  # 一条消息最多一个
    plugin = models.CharField(max_length=64)  # 插件 frontmatter 的 name
    workflow = models.CharField(max_length=255)  # 命中的工作流文件名
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class WorkflowRun(models.Model):
    """一条 eureka 触发后展开的一次工作流运行（一对一挂在 Eureka 上，所以同一条 eureka 只展开一次）。

    和 Eureka 一样只是展示和进度用的表，不进模型上下文。状态写回数据库：刷新页面、重启服务后还知道做到哪一步。
    """

    STATUS_CHOICES = [("pending", "等待"), ("running", "进行中"), ("done", "完成"), ("failed", "失败")]

    eureka = models.OneToOneField(Eureka, on_delete=models.CASCADE, related_name="run")
    plugin = models.CharField(max_length=64)  # 插件名（工作流 frontmatter 的 name）
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True)
    payload = models.JSONField(default=list)  # 触发 eureka 的那份 JSON（已通过 schema 校验）
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    @property
    def session(self):
        return self.eureka.message.session


class StageRun(models.Model):
    """一个阶段的运行记录，随 WorkflowRun 一起创建（全部 pending）。key / title / method 是插件声明的快照。"""

    run = models.ForeignKey(WorkflowRun, on_delete=models.CASCADE, related_name="stages")
    index = models.PositiveIntegerField()  # 顺序（从 0 开始）
    key = models.CharField(max_length=40)
    title = models.CharField(max_length=100)
    method = models.CharField(max_length=100)
    status = models.CharField(max_length=16, choices=WorkflowRun.STATUS_CHOICES, default="pending")
    output = models.JSONField(default=dict, blank=True)  # StageResult.output，给后面的阶段用
    error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["index"]
        constraints = [
            models.UniqueConstraint(fields=["run", "index"], name="unique_stage_index_per_run"),
            models.UniqueConstraint(fields=["run", "key"], name="unique_stage_key_per_run"),
        ]

    def next_log_seq(self) -> int:
        last = self.logs.order_by("-seq").values_list("seq", flat=True).first()
        return (last or 0) + 1


class StageLog(models.Model):
    """插件通过 webhook（ctx.log）打印的一行文字。(stage, seq) 唯一：重发同一行不会重复。"""

    stage = models.ForeignKey(StageRun, on_delete=models.CASCADE, related_name="logs")
    seq = models.PositiveIntegerField()
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["seq"]
        constraints = [models.UniqueConstraint(fields=["stage", "seq"], name="unique_log_seq_per_stage")]


class RequestStat(models.Model):
    """一次请求 Responses API 的记录（core 的 RequestStat）：响应时间和 token。会话统计的平均值由它算出。"""

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="request_stats")
    seconds = models.FloatField()
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    input_messages = models.JSONField(default=list, blank=True)  # 发给模型的对话；内容大，明细列表不带它，展开时单独取
    output_messages = models.JSONField(default=list, blank=True)
    turn = models.ForeignKey("TurnStat", null=True, on_delete=models.CASCADE, related_name="request_stats")  # 所属的轮


class ToolStat(models.Model):
    """一次工具使用的记录（core 的 ToolStat）：工具类型、耗时、读和写的文件数。"""

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="tool_stats")
    name = models.CharField(max_length=64)
    seconds = models.FloatField()
    files_read = models.PositiveIntegerField(default=0)
    files_written = models.PositiveIntegerField(default=0)
    paths = models.JSONField(default=list, blank=True)  # 读 / 写的文件路径（不存内容）
    turn = models.ForeignKey("TurnStat", null=True, on_delete=models.CASCADE, related_name="tool_stats")  # 所属的轮


class TurnStat(models.Model):
    """一个 turn 的记录（core 的 TurnStat）：总耗时、这个 turn 里的请求次数和工具使用次数。"""

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="turn_stats")
    seconds = models.FloatField()
    requests = models.PositiveIntegerField(default=0)
    tools = models.PositiveIntegerField(default=0)
