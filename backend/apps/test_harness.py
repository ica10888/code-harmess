"""适配层测试：用 FakeProvider 验证「后端把 core 的零件拼成一问一答」的行为，不调用真实 API。

HARNESS_LIVE=1 时才会跑文件末尾的 live 测试（打真实 DeepSeek，要 DEEPSEEK_API_KEY、要花钱）。
"""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps import harness
from apps.models import ChatSession
from providers import Reply, ToolCall, Usage


class FakeProvider:
    """按顺序吐预设的 Reply（也可以是要抛的异常），并记下每次收到的东西。"""

    label = "Fake"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.pruned = []

    def chat(self, conversation, tools, system=""):
        self.calls.append({"conversation": copy.deepcopy(conversation), "tools": [t.name for t in tools], "system": system})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        conversation.append({"role": "assistant", "content": "".join(reply.texts) or None})
        return reply

    def tool_results(self, results):
        return [{"role": "tool", "tool_call_id": r.call_id, "content": r.content} for r in results]

    def prune_tool_results(self, conversation, keep_last):
        self.pruned.append(keep_last)
        return 0


def text(s, usage=None):
    return Reply(texts=[s], usage=usage)


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
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.txt"
            path.write_text("secret-content", encoding="utf-8")
            provider = FakeProvider(calls(ToolCall("c1", "read_file", {"path": str(path)})), text("读到了"))
            _, reply = run(provider, "看看 x.txt")
        assert reply == "读到了"
        tool_message = provider.calls[1]["conversation"][-1]
        assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
        assert "secret-content" in tool_message["content"]

    def test_model_only_gets_read_only_tools_and_a_web_prompt(self):
        provider = FakeProvider(text("ok"))
        run(provider)
        assert provider.calls[0]["tools"] == ["read_file", "list_files"]
        system = provider.calls[0]["system"]
        assert "web chat page" in system and "read_file" in system
        assert "run_bash" not in system and "edit_file" not in system

    def test_tool_outside_the_allowed_list_comes_back_as_an_error_result(self):
        provider = FakeProvider(calls(ToolCall("c1", "run_bash", {"command": "echo hi"})), text("好吧"))
        _, reply = run(provider)
        assert reply == "好吧"  # 循环没有崩
        assert "unknown tool: run_bash" in provider.calls[1]["conversation"][-1]["content"]

    def test_tool_needing_approval_is_refused_even_if_configured(self):
        provider = FakeProvider(calls(ToolCall("c1", "run_bash", {"command": "echo hi"})), text("好吧"))
        with override_settings(HARNESS_TOOLS=["read_file", "run_bash"]):
            run(provider)
        assert "denied" in provider.calls[1]["conversation"][-1]["content"]

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
