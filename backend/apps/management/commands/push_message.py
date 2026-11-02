"""模拟「后端主动给前端发文本」：往指定会话写一条 assistant 消息，前端的 SSE 会立刻收到。

用法：py backend/manage.py push_message <会话 id> "要发的文字"
"""

from django.core.management.base import BaseCommand, CommandError

from apps.models import ChatSession, Message


class Command(BaseCommand):
    help = "往指定会话推送一条后端主动发的消息"

    def add_arguments(self, parser):
        parser.add_argument("session_id")
        parser.add_argument("text")

    def handle(self, *args, session_id, text, **options):
        try:
            session = ChatSession.objects.get(pk=session_id)
        except (ChatSession.DoesNotExist, ValueError):
            raise CommandError(f"找不到会话：{session_id}")
        message = Message.objects.create(session=session, role="assistant", content=text)
        self.stdout.write(f"已推送消息 #{message.id}")
