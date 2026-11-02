"""会话接口测试：通过公开的 HTTP 接口、SSE 生成器和管理命令验证行为。"""

import json
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.models import ChatSession, Message
from apps.views import iter_events


def new_session(client, title="测试"):
    return client.post("/api/sessions/", {"title": title}, format="json").json()


class SessionApiTests(TestCase):
    def test_new_session_is_owned_by_the_cookie_that_created_it(self):
        mine, other = APIClient(), APIClient()
        created = new_session(mine)
        assert [s["id"] for s in mine.get("/api/sessions/").json()] == [created["id"]]
        assert other.get("/api/sessions/").json() == []

    def test_other_browser_cannot_read_or_write_my_session(self):
        mine, other = APIClient(), APIClient()
        sid = new_session(mine)["id"]
        assert other.get(f"/api/sessions/{sid}/messages/").status_code == 404
        assert other.post(f"/api/sessions/{sid}/messages/", {"content": "hi"}, format="json").status_code == 404
        assert other.get(f"/api/sessions/{sid}/stream/").status_code == 404

    def test_empty_title_falls_back_to_default(self):
        assert new_session(APIClient(), title="")["title"] == "新会话"


class SessionTitleTests(TestCase):
    def send(self, client, sid, text):
        with mock.patch("apps.services.generate_reply", return_value="ok"):
            client.post(f"/api/sessions/{sid}/messages/", {"content": text}, format="json")

    def title_of(self, client, sid):
        return next(s["title"] for s in client.get("/api/sessions/").json() if s["id"] == sid)

    def test_first_message_becomes_title(self):
        client = APIClient()
        sid = new_session(client, title="")["id"]
        self.send(client, sid, "你好")
        assert self.title_of(client, sid) == "你好"

    def test_long_first_message_is_cut_to_15_chars_with_ellipsis(self):
        client = APIClient()
        sid = new_session(client, title="")["id"]
        self.send(client, sid, "一二三四五六七八九十一二三四五六七")
        assert self.title_of(client, sid) == "一二三四五六七八九十一二三四五..."

    def test_exactly_15_chars_is_kept_whole(self):
        client = APIClient()
        sid = new_session(client, title="")["id"]
        self.send(client, sid, "一二三四五六七八九十一二三四五")
        assert self.title_of(client, sid) == "一二三四五六七八九十一二三四五"

    def test_later_messages_do_not_change_title(self):
        client = APIClient()
        sid = new_session(client, title="")["id"]
        self.send(client, sid, "第一句")
        self.send(client, sid, "第二句")
        assert self.title_of(client, sid) == "第一句"

    def test_stream_sends_title_event_first_time_only(self):
        client = APIClient()
        sid = new_session(client, title="")["id"]
        with mock.patch("apps.services.harness.stream_turn", side_effect=lambda *a: iter([("done", "ok")])):
            first = b"".join(
                client.post(
                    f"/api/sessions/{sid}/messages/stream/", {"content": "你好"}, format="json"
                ).streaming_content
            )
            second = b"".join(
                client.post(
                    f"/api/sessions/{sid}/messages/stream/", {"content": "再见"}, format="json"
                ).streaming_content
            )
        assert b"event: title" in first and "你好".encode() in first
        assert b"event: title" not in second
        assert self.title_of(client, sid) == "你好"


class SendMessageTests(TestCase):
    def test_post_stores_user_message_and_the_reply_from_generate_reply(self):
        client = APIClient()
        sid = new_session(client)["id"]
        with mock.patch("apps.services.generate_reply", return_value="REPLY") as fake:
            res = client.post(f"/api/sessions/{sid}/messages/", {"content": "你好"}, format="json")
        assert res.status_code == 201
        assert [(m["role"], m["content"]) for m in res.json()] == [("user", "你好"), ("assistant", "REPLY")]
        assert fake.call_args.args[1] == "你好"  # generate_reply(session, user_text)
        assert [m.role for m in Message.objects.filter(session_id=sid)] == ["user", "assistant"]

    def test_generate_reply_placeholder_returns_text(self):
        client = APIClient()
        sid = new_session(client)["id"]
        res = client.post(f"/api/sessions/{sid}/messages/", {"content": "hi"}, format="json")
        assert res.json()[1]["content"]  # 占位实现也要回一句话，前端才看得到

    def test_blank_content_is_rejected(self):
        client = APIClient()
        sid = new_session(client)["id"]
        for body in ({"content": ""}, {"content": "   "}, {}):
            assert client.post(f"/api/sessions/{sid}/messages/", body, format="json").status_code == 400
        assert Message.objects.count() == 0

    def test_history_supports_after_cursor(self):
        client = APIClient()
        sid = new_session(client)["id"]
        client.post(f"/api/sessions/{sid}/messages/", {"content": "a"}, format="json")
        history = client.get(f"/api/sessions/{sid}/messages/").json()
        assert [m["role"] for m in history] == ["user", "assistant"]
        later = client.get(f"/api/sessions/{sid}/messages/?after={history[0]['id']}").json()
        assert [m["id"] for m in later] == [history[1]["id"]]


class StreamTests(TestCase):
    def setUp(self):
        self.session = ChatSession.objects.create(owner_key="k")

    def test_new_messages_become_sse_frames_with_id(self):
        m = Message.objects.create(session=self.session, role="assistant", content="主动消息")
        (frame,) = iter_events(self.session, 0, poll_seconds=0, max_loops=1)
        assert frame.startswith(f"id: {m.id}\n")
        payload = json.loads(frame.split("data: ", 1)[1])
        assert payload["content"] == "主动消息" and payload["role"] == "assistant"
        assert frame.endswith("\n\n")

    def test_only_messages_after_the_cursor_are_sent(self):
        first = Message.objects.create(session=self.session, role="user", content="old")
        second = Message.objects.create(session=self.session, role="assistant", content="new")
        frames = list(iter_events(self.session, first.id, poll_seconds=0, max_loops=1))
        assert len(frames) == 1 and frames[0].startswith(f"id: {second.id}\n")

    def test_idle_poll_sends_heartbeat_comment(self):
        assert list(iter_events(self.session, 0, poll_seconds=0, max_loops=2)) == [": keepalive\n\n"] * 2

    def test_stream_endpoint_is_event_stream(self):
        client = APIClient()
        sid = new_session(client)["id"]
        res = client.get(f"/api/sessions/{sid}/stream/")
        assert res.status_code == 200
        assert res["Content-Type"].startswith("text/event-stream")
        assert res["Cache-Control"] == "no-cache"
        res.close()


class PushMessageCommandTests(TestCase):
    def test_command_writes_assistant_message_that_the_stream_then_delivers(self):
        session = ChatSession.objects.create(owner_key="k")
        call_command("push_message", str(session.id), "后端主动说话", stdout=StringIO())
        (frame,) = iter_events(session, 0, poll_seconds=0, max_loops=1)
        assert "后端主动说话" in json.loads(frame.split("data: ", 1)[1])["content"]
        assert Message.objects.get().role == "assistant"
