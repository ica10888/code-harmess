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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Message(models.Model):
    """会话里的一条消息。自增 id 同时是 SSE 的游标：前端只要记住最后收到的 id。"""

    ROLE_CHOICES = [("user", "用户"), ("assistant", "后端")]

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
