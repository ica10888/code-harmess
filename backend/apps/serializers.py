"""序列化层：模型 <-> JSON，以及请求体校验。"""

from rest_framework import serializers

from .models import ChatSession, Message
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
    class Meta:
        model = Message
        fields = ["id", "role", "content", "created_at"]
        read_only_fields = fields


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


class PluginDetailSerializer(PluginSerializer):
    content = serializers.CharField(read_only=True)  # 文件全文


class PluginUpdateSerializer(serializers.Serializer):
    """PATCH 请求体：只能改勾选状态，名称和内容不能改（要改就删了重新上传）。"""

    enabled = serializers.BooleanField()
