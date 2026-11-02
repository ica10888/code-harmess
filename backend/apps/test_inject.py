"""勾选插件后把全文注入对话：后端调 core 的 skills.skills_message，经 Agent.respond 放进上下文。

覆盖：上传时的 frontmatter 校验、勾选的会话插件和默认插件都会注入、没勾选的不注入、坏文件不拖垮请求、
透传到 core 的参数、对话保存和失败回滚。模型用 FakeProvider，不调用真实 API。
"""

import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps import harness
from apps.models import ChatSession
from apps.test_harness import FakeProvider, run, sse_events, text

FM = "---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n"


def skill(name="reviewer", desc="审查代码", body="REVIEW-RULES"):
    return FM.format(name=name, desc=desc, body=body)


def upload_file(filename, content):
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    return SimpleUploadedFile(filename, data)


class UploadValidationTests(TestCase):
    """上传的插件必须有 frontmatter（name + description）：core 注入时要用。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(HARNESS_STORAGE_DIR=tmp.name, HARNESS_DEFAULT_SKILLS_DIR="")
        override.enable()
        self.addCleanup(override.disable)
        self.skills_dir_root = Path(tmp.name)
        self.client = APIClient()
        self.sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        self.url = f"/api/sessions/{self.sid}/plugins/"

    def post(self, filename, content):
        return self.client.post(self.url, {"file": upload_file(filename, content)}, format="multipart")

    def test_a_file_with_name_and_description_is_accepted_and_the_file_name_stays_the_plugin_name(self):
        res = self.post("代码审查.md", skill())
        assert res.status_code == 201 and res.json()["name"] == "代码审查.md"  # 展示名仍是文件名，不是 frontmatter 的 name

    def test_files_core_cannot_use_are_rejected_with_a_reason_and_nothing_is_saved(self):
        bad = {
            "没有 frontmatter": "just text\n",
            "frontmatter 没有闭合": "---\nname: x\ndescription: d\n",
            "缺 name": "---\ndescription: d\n---\nbody\n",
            "缺 description": "---\nname: ok\n---\nbody\n",
            "name 不合规": "---\nname: Bad Name\ndescription: d\n---\nbody\n",
            "空文件": "",
            "二进制": b"\xff\xfe\x00\x01",
        }
        for why, content in bad.items():
            res = self.post("a.md", content)
            assert res.status_code == 400, why
            assert "frontmatter" in res.json()["detail"] or "name" in res.json()["detail"] or "description" in res.json()["detail"], why
        assert not (self.skills_dir_root / "input" / self.sid / "skills").exists()

    def test_the_rejection_message_says_what_is_expected(self):
        detail = self.post("a.md", "no frontmatter").json()["detail"]
        assert "name" in detail and "description" in detail

    def test_the_file_is_still_stored_byte_for_byte_when_valid(self):
        body = skill(body="中文 ✓\r\n第二行")
        self.post("a.md", body)
        saved = self.skills_dir_root / "input" / self.sid / "skills" / "a.md"
        assert saved.read_bytes() == body.encode("utf-8")


class RecordingProvider(FakeProvider):
    def chat(self, conversation, tools, system="", on_text=None, model=None, effort=None):
        return super().chat(conversation, tools, system, on_text)


class InjectionTests(TestCase):
    """勾选的插件（会话自己的 + 默认的）全文在这一轮被放进模型看到的上下文。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.defaults = self.tmp / "defaults"
        self.defaults.mkdir()
        (self.defaults / "默认.md").write_bytes(skill("default-one", "默认的", "DEFAULT-BODY").encode("utf-8"))
        override = override_settings(HARNESS_STORAGE_DIR=str(self.tmp / "s"), HARNESS_DEFAULT_SKILLS_DIR=str(self.defaults))
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        self.plugins = f"/api/sessions/{self.sid}/plugins/"

    def add(self, filename, content):
        res = self.client.post(self.plugins, {"file": upload_file(filename, content)}, format="multipart")
        assert res.status_code == 201, res.content
        return res

    def check(self, name, enabled=True):
        from urllib.parse import quote

        res = self.client.patch(f"{self.plugins}{quote(name)}/", {"enabled": enabled}, format="json")
        assert res.status_code == 200
        return res

    def user_messages(self, provider, call=0):
        return [m["content"] for m in provider.calls[call]["conversation"] if m.get("role") == "user"]

    def send(self, provider, content="请处理", **extra):
        with mock.patch("apps.harness.get_provider", return_value=provider):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/", {"content": content, **extra}, format="json")
        assert res.status_code == 201, res.content
        return res

    # --- 注入内容 ---

    def test_a_checked_session_plugin_is_injected_in_full_before_the_users_message(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        self.check("mine.md")
        provider = RecordingProvider(text("ok"))

        self.send(provider, "请处理")

        users = self.user_messages(provider)
        assert len(users) == 2 and "MINE-BODY" in users[0] and '<skill name="mine">' in users[0]
        assert users[1] == "请处理"  # 用户的话原样在最后

    def test_a_checked_default_plugin_is_injected_too(self):
        self.check("默认.md")
        provider = RecordingProvider(text("ok"))

        self.send(provider)

        assert "DEFAULT-BODY" in self.user_messages(provider)[0]

    def test_defaults_come_before_the_sessions_own_in_the_injected_message(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        self.check("mine.md")
        self.check("默认.md")
        provider = RecordingProvider(text("ok"))

        self.send(provider)

        message = self.user_messages(provider)[0]
        assert message.index("DEFAULT-BODY") < message.index("MINE-BODY")

    def test_unchecked_plugins_are_not_injected(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        provider = RecordingProvider(text("ok"))

        self.send(provider)

        assert self.user_messages(provider) == ["请处理"]  # 默认插件也没勾，所以只有用户这一句

    def test_unchecking_stops_the_injection_for_the_next_message(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        self.check("mine.md")
        self.check("mine.md", enabled=False)
        provider = RecordingProvider(text("ok"))

        self.send(provider)

        assert all("MINE-BODY" not in m for m in self.user_messages(provider))

    def test_another_sessions_checked_plugins_do_not_leak_in(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        self.check("mine.md")
        other = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        provider = RecordingProvider(text("ok"))

        with mock.patch("apps.harness.get_provider", return_value=provider):
            self.client.post(f"/api/sessions/{other}/messages/", {"content": "hi"}, format="json")

        assert all("MINE-BODY" not in m for m in self.user_messages(provider))

    # --- 对话里怎么留 ---

    def test_the_injected_skill_is_saved_in_the_conversation_once_and_not_repeated_every_turn(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        self.check("mine.md")
        provider = RecordingProvider(text("one"), text("two"), text("three"))

        self.send(provider, "第一句")
        self.send(provider, "第二句")
        self.send(provider, "第三句")

        # 和 /skill名 一致：第一次注入后留在历史里，之后每轮都带着它，但不会重复注入
        third = self.user_messages(provider, call=2)
        assert sum("MINE-BODY" in m for m in third) == 1
        saved = ChatSession.objects.get(pk=self.sid).conversation
        assert sum("MINE-BODY" in str(m.get("content")) for m in saved) == 1

    def test_a_newly_checked_plugin_is_injected_on_the_next_message_even_in_a_long_conversation(self):
        provider = RecordingProvider(text("one"), text("two"))
        self.send(provider, "第一句")
        self.add("late.md", skill("late", "后来的", "LATE-BODY"))
        self.check("late.md")

        self.send(provider, "第二句")

        assert any("LATE-BODY" in m for m in self.user_messages(provider, call=1))

    def test_the_user_visible_message_history_does_not_contain_the_skill_text(self):
        # 注入的是发给模型的上下文，不是聊天记录：页面上看到的消息里不该出现整份插件正文
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        self.check("mine.md")

        self.send(RecordingProvider(text("ok")), "请处理")

        shown = self.client.get(f"/api/sessions/{self.sid}/messages/").json()
        assert [m["content"] for m in shown if m["role"] == "user"] == ["请处理"]

    # --- 异常情况 ---

    def test_a_checked_plugin_whose_file_has_no_frontmatter_is_skipped_and_the_request_still_works(self):
        # 老数据：以前允许上传任何 .md，里面可能有没有 frontmatter 的
        skills_dir = Path(harness.skills_dir(ChatSession.objects.get(pk=self.sid)))
        skills_dir.mkdir(parents=True)
        (skills_dir / "old.md").write_text("no frontmatter", encoding="utf-8")
        self.check("old.md")
        provider = RecordingProvider(text("ok"))

        res = self.send(provider)

        assert all("no frontmatter" not in m for m in self.user_messages(provider))
        assert res.json()[1]["content"] == "ok"  # 请求没有被一个坏文件拖垮

    def test_a_checked_plugin_deleted_from_disk_is_skipped_silently(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        self.check("mine.md")
        (Path(harness.skills_dir(ChatSession.objects.get(pk=self.sid))) / "mine.md").unlink()
        provider = RecordingProvider(text("ok"))

        self.send(provider)

        assert self.user_messages(provider) == ["请处理"]

    def test_a_failed_model_call_leaves_no_trace_of_the_injected_skill_in_the_saved_conversation(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        self.check("mine.md")
        failing = RecordingProvider(RuntimeError("boom"))

        res = self.send(failing)

        assert "调用模型失败" in res.json()[1]["content"]
        assert ChatSession.objects.get(pk=self.sid).conversation == []

    def test_streaming_injects_the_same_way(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        self.check("mine.md")
        provider = RecordingProvider(text("ok"))

        with mock.patch("apps.harness.get_provider", return_value=provider):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/stream/", {"content": "请处理"}, format="json")
            events = sse_events(res)

        assert events[-1][0] == "done"
        assert "MINE-BODY" in self.user_messages(provider)[0]

    def test_model_and_effort_still_pass_through_with_skills_injected(self):
        class Spy(RecordingProvider):
            def chat(self, conversation, tools, system="", on_text=None, model=None, effort=None):
                self.seen = (model, effort)
                return FakeProvider.chat(self, conversation, tools, system, on_text)

        self.check("默认.md")
        provider = Spy(text("ok"))

        self.send(provider, model="deepseek-v4-pro", effort="max")

        assert provider.seen == ("deepseek-v4-pro", "max")
        assert "DEFAULT-BODY" in self.user_messages(provider)[0]
