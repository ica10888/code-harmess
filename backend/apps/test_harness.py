"""适配层测试：用 FakeProvider 验证「后端把 core 的零件拼成一问一答」的行为，不调用真实 API。

HARNESS_LIVE=1 时才会跑文件末尾的 live 测试（打真实 DeepSeek，要 DEEPSEEK_API_KEY、要花钱）。
"""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps import harness, services
from apps.models import ChatSession, Message
from providers import Reply, ToolCall, Usage


class FakeProvider:
    """按顺序吐预设的 Reply（也可以是要抛的异常），并记下每次收到的东西。"""

    label = "Fake"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.pruned = []

    def chat(self, conversation, tools, system="", on_text=None):
        self.calls.append({"conversation": copy.deepcopy(conversation), "tools": [t.name for t in tools], "system": system})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        conversation.append({"role": "assistant", "content": "".join(reply.texts) or None})
        for piece in reply.texts if on_text else []:
            on_text(piece[: len(piece) // 2])  # 拆成两段，像真的流式
            on_text(piece[len(piece) // 2 :])
        return reply

    def tool_results(self, results):
        return [{"role": "tool", "tool_call_id": r.call_id, "content": r.content} for r in results]

    def prune_tool_results(self, conversation, keep_last):
        self.pruned.append(keep_last)
        return 0


def text(s, usage=None):
    return Reply(texts=[s], usage=usage)


def picks(*names):
    """skill_selector 那次「选 skill」请求的模型回复：{"skills": [名字, ...]}。排在正式回复之前。"""
    return text(json.dumps({"skills": list(names)}))


def calls(*tool_calls):
    return Reply(tool_calls=list(tool_calls))


def run(provider, user_text="hi", session=None):
    session = session or ChatSession.objects.create(owner_key="k")
    with mock.patch("apps.harness.get_provider", return_value=provider):
        return session, harness.run_turn(session, user_text)


class RunTurnTests(TestCase):
    def test_returns_the_model_text_and_saves_the_conversation(self):
        session, reply = run(FakeProvider(text("你好")), "hi")
        assert reply == "你好"
        saved = ChatSession.objects.get(pk=session.pk).conversation
        assert [m["role"] for m in saved] == ["user", "assistant"]
        assert saved[0]["content"] == "hi"

    def test_conversation_carries_over_to_the_next_turn(self):
        provider = FakeProvider(text("one"), text("two"))
        session, _ = run(provider, "first")
        run(provider, "second", session=ChatSession.objects.get(pk=session.pk))
        sent = provider.calls[1]["conversation"]
        assert [m["content"] for m in sent if m["role"] == "user"] == ["first", "second"]

    def test_tool_call_is_executed_and_result_goes_back_to_the_model(self):
        with tempfile.TemporaryDirectory() as storage, override_settings(HARNESS_STORAGE_DIR=storage):
            session = ChatSession.objects.create(owner_key="k")
            workspace = Path(harness.workspace_dir(session))
            workspace.mkdir(parents=True)
            (workspace / "x.txt").write_text("secret-content", encoding="utf-8")
            provider = FakeProvider(calls(ToolCall("c1", "read_file", {"path": "x.txt"})), text("读到了"))
            _, reply = run(provider, "看看 x.txt", session=session)
        assert reply == "读到了"
        tool_message = provider.calls[1]["conversation"][-1]
        assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
        assert "secret-content" in tool_message["content"]

    def test_stats_are_saved_per_turn_with_their_own_requests_and_tools(self):
        session = ChatSession.objects.create(owner_key="k")
        first = FakeProvider(calls(ToolCall("c1", "no_such_tool", {})), text("a", usage=Usage(10, 2)))
        run(first, "one", session=session)
        run(FakeProvider(text("b", usage=Usage(20, 3))), "two", session=session)

        one, two = session.turn_stats.order_by("id")
        assert [(r.input_tokens, r.output_tokens) for r in one.request_stats.order_by("id")][-1] == (10, 2)
        assert [t.name for t in one.tool_stats.all()] == ["no_such_tool"]
        assert one.request_stats.count() == one.requests and one.tool_stats.count() == one.tools
        assert [(r.input_tokens, r.output_tokens) for r in two.request_stats.order_by("id")][-1] == (20, 3)
        assert two.tool_stats.count() == 0
        assert session.request_stats.count() == one.requests + two.requests  # 没有记录漏挂到轮上

    def test_model_gets_all_workspace_tools_and_a_web_prompt(self):
        provider = FakeProvider(text("ok"))
        run(provider)
        assert provider.calls[0]["tools"] == ["read_file", "list_files", "edit_file", "run_bash"]
        system = provider.calls[0]["system"]
        assert "web chat page" in system and "read_file" in system
        assert "edit_file" in system and "run_bash" in system

    def test_tool_outside_the_allowed_list_comes_back_as_an_error_result(self):
        provider = FakeProvider(calls(ToolCall("c1", "no_such_tool", {})), text("好吧"))
        _, reply = run(provider)
        assert reply == "好吧"  # 循环没有崩
        assert "unknown tool: no_such_tool" in provider.calls[1]["conversation"][-1]["content"]

    def test_run_bash_runs_without_asking_for_approval(self):
        provider = FakeProvider(calls(ToolCall("c1", "run_bash", {"command": "echo hi"})), text("好了"))
        _, reply = run(provider)
        assert reply == "好了"
        assert "hi" in provider.calls[1]["conversation"][-1]["content"]

    def test_endless_tool_calls_pause_after_the_round_limit_with_every_call_answered(self):
        looping = [calls(ToolCall(f"c{i}", "list_files", {"path": "."})) for i in range(3)]
        with override_settings(HARNESS_MAX_TOOL_ROUNDS=3):
            session, reply = run(FakeProvider(*looping))
        assert "3 轮" in reply
        saved = ChatSession.objects.get(pk=session.pk).conversation
        assert [m["role"] for m in saved].count("tool") == 3  # 每个 tool_call 都有回复，下一句话才不会被 API 拒绝

    def test_prunes_old_tool_results_only_when_input_exceeds_the_budget(self):
        provider = FakeProvider(text("a", usage=Usage(100, 1)), text("b", usage=Usage(5, 1)))
        with override_settings(HARNESS_CONTEXT_BUDGET=10, HARNESS_KEEP_TOOL_RESULTS=5):
            session, _ = run(provider)
            run(provider, session=session)
        assert provider.pruned == [5]

    def test_system_prompt_states_the_real_model_identity(self):
        provider = FakeProvider(text("ok"))
        with override_settings(DEEPSEEK_MODEL="deepseek-v4-pro"):
            run(provider)
        system = provider.calls[0]["system"]
        assert "powered by DeepSeek (model: deepseek-v4-pro)" in system
        assert "never claim to be Claude" in system  # 明确禁止冒充别家模型

    def test_empty_model_answer_gets_a_placeholder_text(self):
        _, reply = run(FakeProvider(Reply()))
        assert reply


class ProviderConfigTests(TestCase):
    def setUp(self):
        harness._provider = None
        self.addCleanup(setattr, harness, "_provider", None)

    def test_provider_is_built_from_django_settings_not_from_environment(self):
        with (
            override_settings(DEEPSEEK_API_KEY="sk-from-ini", DEEPSEEK_MODEL="m1", DEEPSEEK_BASE_URL="http://u"),
            mock.patch("apps.harness.DeepSeekProvider") as cls,
        ):
            provider = harness.get_provider()
            assert harness.get_provider() is provider  # 只创建一次
        cls.assert_called_once_with(model="m1", api_key="sk-from-ini", base_url="http://u")

    def test_missing_key_tells_the_user_to_edit_config_ini(self):
        with override_settings(DEEPSEEK_API_KEY=""):
            with self.assertRaisesMessage(RuntimeError, "config.ini"):
                harness.get_provider()

    def test_placeholder_key_is_treated_as_missing(self):
        with override_settings(DEEPSEEK_API_KEY="sk-your-key-here"):
            with self.assertRaisesMessage(RuntimeError, "config.ini"):
                harness.get_provider()


class ApiIntegrationTests(TestCase):
    def post(self, client, session_id, content="hi"):
        return client.post(f"/api/sessions/{session_id}/messages/", {"content": content}, format="json")

    def test_api_reply_comes_from_the_provider(self):
        client = APIClient()
        sid = client.post("/api/sessions/", {}, format="json").json()["id"]
        with mock.patch("apps.harness.get_provider", return_value=FakeProvider(text("来自模型"))):
            res = self.post(client, sid)
        assert [(m["role"], m["content"]) for m in res.json()] == [("user", "hi"), ("assistant", "来自模型")]

    def test_provider_failure_is_shown_as_a_message_and_does_not_corrupt_the_conversation(self):
        client = APIClient()
        sid = client.post("/api/sessions/", {}, format="json").json()["id"]
        failing = FakeProvider(RuntimeError("boom"))
        with mock.patch("apps.harness.get_provider", return_value=failing):
            res = self.post(client, sid, "会失败的一句")
        assert res.status_code == 201
        assert "调用模型失败" in res.json()[1]["content"] and "boom" in res.json()[1]["content"]
        assert ChatSession.objects.get(pk=sid).conversation == []  # 失败的这一轮不留在对话里
        ok = FakeProvider(text("恢复了"))
        with mock.patch("apps.harness.get_provider", return_value=ok):
            self.post(client, sid, "再来")
        assert [m["content"] for m in ok.calls[0]["conversation"]] == ["再来"]

    def test_conversation_is_not_exposed_by_the_api(self):
        client = APIClient()
        created = client.post("/api/sessions/", {}, format="json").json()
        assert "conversation" not in created


@unittest.skipUnless(os.environ.get("HARNESS_LIVE"), "设 HARNESS_LIVE=1 才打真实 DeepSeek API（要花钱）")
class LiveTests(TestCase):
    def test_model_uses_tools_through_the_web_api_and_remembers_the_conversation(self):
        client = APIClient()
        sid = client.post("/api/sessions/", {}, format="json").json()["id"]
        first = client.post(
            f"/api/sessions/{sid}/messages/",
            {"content": "用工具看一下当前目录里有没有 AGENTS.md 这个文件，有就回答「有」，没有就回答「没有」。"},
            format="json",
        ).json()
        assert first[1]["role"] == "assistant" and "有" in first[1]["content"]
        saved = ChatSession.objects.get(pk=sid).conversation
        assert any(m["role"] == "tool" for m in saved)  # 真的调用了工具

        second = client.post(f"/api/sessions/{sid}/messages/", {"content": "我上一句让你找的是哪个文件？"}, format="json").json()
        assert "AGENTS.md" in second[1]["content"]  # 记得上一轮


def sse_events(response):
    """把流式响应解析成 [(event, data)]。"""
    body = b"".join(response.streaming_content).decode("utf-8")
    events = []
    for frame in filter(None, body.split("\n\n")):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


class StreamTurnTests(TestCase):
    def stream(self, provider, user_text="hi", session=None):
        session = session or ChatSession.objects.create(owner_key="k")
        with mock.patch("apps.harness.get_provider", return_value=provider):
            return session, list(harness.stream_turn(session, user_text))

    def test_yields_text_pieces_then_the_full_final_text_and_saves_the_conversation(self):
        session, events = self.stream(FakeProvider(text("你好世界")))
        assert [e for e in events if e[0] == "delta"] == [("delta", "你好"), ("delta", "世界")]
        assert events[-1] == ("done", "你好世界")
        # 轮一开始就有一条 stats（0/1/0，占位轮已建），请求结束一条（1/1/0），轮结束一条；都在 done 之前、统计已存库
        stats = [d for k, d in events if k == "stats"]
        assert stats == [
            {"request_count": 0, "turn_count": 1, "tool_count": 0, "running": True},
            {"request_count": 1, "turn_count": 1, "tool_count": 0, "running": True},
            {"request_count": 1, "turn_count": 1, "tool_count": 0, "running": False},  # 轮结束：前端据此重取平均值和明细
        ]
        assert [m["role"] for m in ChatSession.objects.get(pk=session.pk).conversation] == ["user", "assistant"]

    def test_final_text_includes_the_pause_notice_that_was_never_streamed(self):
        looping = [calls(ToolCall(f"c{i}", "list_files", {"path": "."})) for i in range(2)]
        with override_settings(HARNESS_MAX_TOOL_ROUNDS=2):
            _, events = self.stream(FakeProvider(*looping))
        kind, final = events[-1]
        assert kind == "done" and "2 轮" in final

    def test_failure_is_raised_to_the_caller_and_the_conversation_is_untouched(self):
        session = ChatSession.objects.create(owner_key="k")
        with self.assertRaisesMessage(RuntimeError, "boom"):
            self.stream(FakeProvider(RuntimeError("boom")), session=session)
        assert ChatSession.objects.get(pk=session.pk).conversation == []


class InProgressTurnTests(TestCase):
    def test_detail_shows_the_unfinished_turn_with_the_records_so_far(self):
        """轮还没结束：占位轮已在明细里，已发生的请求和工具挂在它名下，次数按已有记录算。"""
        session = ChatSession.objects.create(owner_key="k")
        turn = harness.start_turn(session)
        from agent import RequestStat, ToolStat

        harness.record_stat(session, turn, "request", RequestStat(1.5, 10, 2))
        harness.record_stat(session, turn, "tool", ToolStat("read_file", 0.1, 1, 0, ["a.txt"]))
        row = services.session_stats_detail(session)["turns"][0]
        assert (row["finished"], row["requests"], row["tools"]) == (False, 1, 1)
        assert [r["input_tokens"] for r in row["request_list"]] == [10] and row["tool_list"][0]["name"] == "read_file"
        assert services.session_stats(session)["turn"]["avg_seconds"] is None  # 平均值只算完成的轮

    def test_turn_stat_is_filled_in_when_the_turn_ends(self):
        session = ChatSession.objects.create(owner_key="k")
        with mock.patch("apps.harness.get_provider", return_value=FakeProvider(text("好"))):
            list(harness.stream_turn(session, "hi"))
        row = services.session_stats_detail(session)["turns"][0]
        assert (row["finished"], row["requests"], row["tools"]) == (True, 1, 0)
        assert session.turn_stats.count() == 1  # 占位的就是最终那条，没有多建

    def test_a_failed_turn_is_settled_not_left_in_progress(self):
        session = ChatSession.objects.create(owner_key="k")
        with self.assertRaises(RuntimeError):
            with mock.patch("apps.harness.get_provider", return_value=FakeProvider(RuntimeError("boom"))):
                list(harness.stream_turn(session, "hi"))
        assert session.turn_stats.get().finished is True


class StreamDisconnectTests(TestCase):
    def test_client_disconnect_mid_stream_still_saves_the_reply_and_counts(self):
        """客户端中途断开（刷新页面、关标签）：模型照样跑完，回复和计数都要存下来，不能只剩一条用户消息。"""
        session = ChatSession.objects.create(owner_key="k")
        with mock.patch("apps.harness.get_provider", return_value=FakeProvider(text("你好世界"))):
            events = services.stream_user_message(session, "问")
            while next(events)[0] != "user":
                pass
            events.close()  # 模拟 Django 在客户端断开后关闭响应的生成器
        assert [(m.role, m.content) for m in Message.objects.filter(session=session)] == [("user", "问"), ("assistant", "你好世界")]
        assert ChatSession.objects.get(pk=session.pk).request_count == 1


class StreamMessageApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        self.url = f"/api/sessions/{self.sid}/messages/stream/"

    def post(self, provider, content="hi", client=None):
        # 流是惰性的：要在 mock 还生效时就读完，否则消费流时 get_provider 已经是真的了
        with mock.patch("apps.harness.get_provider", return_value=provider):
            res = (client or self.client).post(self.url, {"content": content}, format="json")
            if res["Content-Type"].startswith("text/event-stream"):
                res.events = sse_events(res)
            return res

    def test_streams_user_then_deltas_then_done_and_stores_both_messages(self):
        res = self.post(FakeProvider(text("你好世界")), "问")
        assert res["Content-Type"].startswith("text/event-stream") and res["Cache-Control"] == "no-cache"
        events = res.events
        assert [e for e, _ in events if e != "stats"] == ["title", "user", "delta", "delta", "done"]
        assert [e for e, _ in events].count("stats") == 3
        assert events[0][1] == {"title": "问"}  # 第一句话同时成为会话标题
        assert events[1][1]["content"] == "问" and events[1][1]["role"] == "user"
        assert "".join(d["text"] for e, d in events if e == "delta") == "你好世界"
        done = events[-1][1]
        assert done["role"] == "assistant" and done["content"] == "你好世界"
        stored = list(Message.objects.filter(session_id=self.sid))
        assert [(m.role, m.content) for m in stored] == [("user", "问"), ("assistant", "你好世界")]
        assert done["id"] == stored[1].id  # 前端靠 id 把流式气泡换成这条真消息

    def test_model_failure_ends_with_a_done_event_carrying_the_error_text(self):
        events = self.post(FakeProvider(RuntimeError("boom"))).events
        assert [e for e, _ in events[:2]] == ["title", "user"]
        kind, done = events[-1]
        assert kind == "done" and "调用模型失败" in done["content"] and "boom" in done["content"]
        assert ChatSession.objects.get(pk=self.sid).conversation == []  # 这一轮不进对话，重发即可

    def test_blank_content_is_rejected_before_streaming(self):
        assert self.post(FakeProvider(), "   ").status_code == 400
        assert Message.objects.count() == 0

    def test_other_browser_gets_404(self):
        assert self.post(FakeProvider(), client=APIClient()).status_code == 404


class WorkspaceTests(TestCase):
    """每个会话一个目录：<HARNESS_STORAGE_DIR>/input/<会话id>/file，工具只能在里面读写。"""

    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        override = override_settings(HARNESS_STORAGE_DIR=self.storage.name, HARNESS_TOOLS=["read_file", "list_files", "edit_file"])
        override.enable()
        self.addCleanup(override.disable)

    def test_workspace_path_is_storage_input_session_id_file(self):
        session = ChatSession.objects.create(owner_key="k")
        expected = Path(self.storage.name) / "input" / str(session.id) / "file"
        assert Path(harness.workspace_dir(session)) == expected

    def test_directory_is_created_when_the_model_first_uses_a_tool(self):
        session = ChatSession.objects.create(owner_key="k")
        workspace = Path(harness.workspace_dir(session))
        assert not workspace.exists()  # 只是聊天、没用工具，就不建目录

        provider = FakeProvider(calls(ToolCall("c1", "list_files", {})), text("空的"))
        run(provider, session=session)

        assert workspace.is_dir()

    def test_edit_file_puts_the_file_in_this_sessions_directory(self):
        session = ChatSession.objects.create(owner_key="k")
        provider = FakeProvider(
            calls(ToolCall("c1", "edit_file", {"path": "notes/a.txt", "old_str": "", "new_str": "hello"})), text("写好了")
        )

        run(provider, session=session)

        written = Path(self.storage.name) / "input" / str(session.id) / "file" / "notes" / "a.txt"
        assert written.read_text(encoding="utf-8") == "hello"
        assert not (Path.cwd() / "notes" / "a.txt").exists()  # 没有落到项目目录

    def test_files_written_in_one_turn_are_listed_and_readable_in_the_next(self):
        session = ChatSession.objects.create(owner_key="k")
        run(FakeProvider(calls(ToolCall("c1", "edit_file", {"path": "a.txt", "old_str": "", "new_str": "内容"})), text("ok")), session=session)

        provider = FakeProvider(
            calls(ToolCall("c2", "list_files", {}), ToolCall("c3", "read_file", {"path": "a.txt"})), text("看到了")
        )
        run(provider, session=ChatSession.objects.get(pk=session.pk))

        results = [m["content"] for m in provider.calls[1]["conversation"] if m["role"] == "tool"][-2:]
        assert "a.txt" in results[0] and "内容" in results[1]

    def test_two_sessions_do_not_see_each_other(self):
        a, b = ChatSession.objects.create(owner_key="k"), ChatSession.objects.create(owner_key="k")
        run(FakeProvider(calls(ToolCall("c1", "edit_file", {"path": "mine.txt", "old_str": "", "new_str": "x"})), text("ok")), session=a)

        provider = FakeProvider(calls(ToolCall("c2", "list_files", {})), text("ok"))
        run(provider, session=b)

        assert "mine.txt" not in provider.calls[1]["conversation"][-1]["content"]

    def test_escaping_the_session_directory_comes_back_as_an_error_result(self):
        (Path(self.storage.name) / "secret.txt").write_text("top secret", encoding="utf-8")
        session = ChatSession.objects.create(owner_key="k")
        provider = FakeProvider(calls(ToolCall("c1", "read_file", {"path": "../../../secret.txt"})), text("读不了"))

        _, reply = run(provider, session=session)

        assert reply == "读不了"  # 循环没崩
        result = provider.calls[1]["conversation"][-1]["content"]
        assert "outside the workspace" in result and "top secret" not in result

    def test_prompt_tells_the_model_it_works_in_a_private_directory(self):
        provider = FakeProvider(text("ok"))
        run(provider)
        system = provider.calls[0]["system"]
        assert "relative" in system and "workspace" in system
        assert self.storage.name not in system  # 不把服务器上的真实路径告诉模型

    def test_read_only_default_has_no_edit_file(self):
        with override_settings(HARNESS_TOOLS=["read_file", "list_files"]):
            provider = FakeProvider(text("ok"))
            run(provider)
        assert provider.calls[0]["tools"] == ["read_file", "list_files"]

    def test_run_bash_is_only_offered_when_configured(self):
        with override_settings(HARNESS_TOOLS=["read_file", "run_bash"]):
            provider = FakeProvider(text("ok"))
            run(provider)
        assert provider.calls[0]["tools"] == ["read_file", "run_bash"]


class RunningFlagTests(TestCase):
    def test_session_list_says_whether_a_turn_is_still_running(self):
        """刷新 / 切回来的页面靠 running 知道这一轮还在后端跑，要继续跟进计数和选用插件提示。"""
        client = APIClient()
        sid = client.post("/api/sessions/", {}, format="json").json()["id"]
        assert client.get("/api/sessions/").json()[0]["running"] is False

        session = ChatSession.objects.get(pk=sid)
        turn = harness.start_turn(session)
        assert client.get("/api/sessions/").json()[0]["running"] is True

        from agent import TurnStat

        harness.record_stat(session, turn, "turn", TurnStat(1.0, 0, 0))
        assert client.get("/api/sessions/").json()[0]["running"] is False
