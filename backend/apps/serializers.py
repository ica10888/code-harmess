"""序列化层：模型 <-> JSON，以及请求体校验。"""

from rest_framework import serializers

from .models import ChatSession, Message, WorkflowRun
from .options import Effort, Model


class ChatSessionSerializer(serializers.ModelSerializer):
    # title 可以不传或传空串，用模型默认值
    title = serializers.CharField(max_length=100, required=False, allow_blank=True)

    class Meta:
        model = ChatSession
        fields = ["id", "title", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_title(self, value):
        return value.strip() or ChatSession._meta.get_field("title").default


class MessageSerializer(serializers.ModelSerializer):
    # 这条消息有 eureka 时刻就带上 {"text": ...}，没有就是 null。它只是展示用的，不是消息内容，不会进模型上下文
    eureka = serializers.SerializerMethodField()
    notice = serializers.SerializerMethodField()  # 给用户看的提示，没有就是 null

    class Meta:
        model = Message
        fields = ["id", "role", "content", "created_at", "eureka", "notice"]
        read_only_fields = fields

    def get_notice(self, message):
        return message.notice or None

    def get_eureka(self, message):
        eureka = getattr(message, "eureka", None)  # 反向一对一：没有就是 None（这里不会触发额外查询报错）
        if not eureka:
            return None
        run = getattr(eureka, "run", None)  # 展开了工作流才有；刷新页面后前端靠它继续显示进度
        return {"text": eureka.text, "run": {"id": run.id, "status": run.status} if run else None}


class SendMessageSerializer(serializers.Serializer):
    # CharField 默认会 strip，所以只有空白的内容也会被拒绝
    content = serializers.CharField()
    # 本次请求用哪个模型、多强的推理；都可选，不给就用服务端默认。给了就必须是枚举里的值（空串也不行）
    model = serializers.ChoiceField(choices=[m.value for m in Model], required=False)
    effort = serializers.ChoiceField(choices=[e.value for e in Effort], required=False)


class PluginSerializer(serializers.Serializer):
    """插件（列表用）：不带文件内容。名称就是文件名。"""

    name = serializers.CharField(read_only=True)
    enabled = serializers.BooleanField()
    default = serializers.BooleanField(read_only=True)  # 默认插件：所有会话都有，不能删除
    notice = serializers.CharField(read_only=True, required=False)  # 勾选后的提醒（例如工作流没有同名插件）；没有就不出现


class PluginDetailSerializer(PluginSerializer):
    content = serializers.CharField(read_only=True)  # 文件全文


class PluginUpdateSerializer(serializers.Serializer):
    """PATCH 请求体：只能改勾选状态，名称和内容不能改（要改就删了重新上传）。"""

    enabled = serializers.BooleanField()


class WebhookLogSerializer(serializers.Serializer):
    """插件回传的一行日志：seq 从 1 开始，text 是一行非空文字。"""

    seq = serializers.IntegerField(min_value=1)
    text = serializers.CharField(trim_whitespace=False)  # 单条的截断在写库那一步统一做

    def validate_text(self, value):
        return value

    def to_internal_value(self, data):
        # DRF 的 CharField 会把数字、布尔悄悄转成字符串（5 → "5"）；这个接口只收真正的字符串，别的一律 400
        if isinstance(data, dict) and "text" in data and not isinstance(data["text"], str):
            raise serializers.ValidationError({"text": "必须是字符串"})
        if isinstance(data, dict) and "seq" in data and (isinstance(data["seq"], bool) or not isinstance(data["seq"], int)):
            raise serializers.ValidationError({"seq": "必须是整数"})
        return super().to_internal_value(data)


class EurekaRunSerializer(serializers.ModelSerializer):
    """进度接口返回的运行：状态 + 每个阶段的状态和日志。不返回令牌，也不返回触发它的原始 JSON。"""

    stages = serializers.SerializerMethodField()

    class Meta:
        model = WorkflowRun
        fields = ["id", "plugin", "status", "error", "stages", "created_at", "finished_at"]
        read_only_fields = fields

    def get_stages(self, run):
        return [
            {
                "key": s.key,
                "title": s.title,
                "status": s.status,
                "error": s.error,
                "logs": [log.text for log in s.logs.all()],
            }
            for s in run.stages.all()
        ]
