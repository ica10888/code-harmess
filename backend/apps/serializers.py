"""序列化层：模型 <-> JSON，以及请求体校验。"""

from rest_framework import serializers

from .models import ChatSession, Message


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
