"""先选后载：后端把默认插件和会话插件交给 core 的 skill_selector，正式请求前先选，再把选中的全文放进对话。没有勾选。

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
from apps.test_harness import FakeProvider, picks, sse_events, text

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


class SelectingProvider(FakeProvider):
    """第一次 chat 是「选 skill」的请求（没有工具），之后才是正式回复；记下每次的 model / effort。"""

    def chat(self, conversation, tools, system="", on_text=None, model=None, effort=None):
        self.options = [*getattr(self, "options", []), (model, effort)]
        return super().chat(conversation, tools, system, on_text)


class SelectionTests(TestCase):
    """后端把默认插件和会话插件都交给 core：正式请求前先选，再加载选中的。没有「勾选」。"""

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

    def send(self, provider, content="请处理", **extra):
        with mock.patch("apps.harness.get_provider", return_value=provider):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/", {"content": content, **extra}, format="json")
        assert res.status_code == 201, res.content
        return res

    def users(self, provider, call):
        return [m["content"] for m in provider.calls[call]["conversation"] if m.get("role") == "user"]

    # --- 选 ---

    def test_the_model_picks_a_default_plugin_and_it_is_loaded_before_the_users_message(self):
        provider = SelectingProvider(picks("default-one"), text("ok"))

        self.send(provider, "请处理")

        assert len(provider.calls) == 2
        assert provider.calls[0]["tools"] == []  # 第一次是选择请求
        assert "default-one" in str(provider.calls[0]["conversation"][0]["content"])  # 目录里有它
        users = self.users(provider, 1)
        assert len(users) == 2 and "DEFAULT-BODY" in users[0] and '<skill name="default-one">' in users[0]
        assert users[1] == "请处理"  # 用户的话原样在最后

    def test_the_session_s_own_plugins_are_candidates_too(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        provider = SelectingProvider(picks("mine"), text("ok"))

        self.send(provider)

        assert "mine" in str(provider.calls[0]["conversation"][0]["content"])
        assert "MINE-BODY" in self.users(provider, 1)[0]

    def test_only_the_picked_plugins_are_loaded(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        provider = SelectingProvider(picks("mine"), text("ok"))

        self.send(provider)

        assert all("DEFAULT-BODY" not in m for m in self.users(provider, 1))

    def test_defaults_come_before_the_sessions_own_in_the_catalog(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        provider = SelectingProvider(picks(), text("ok"))

        self.send(provider)

        catalog = str(provider.calls[0]["conversation"][0]["content"])
        assert catalog.index("default-one") < catalog.index("mine")

    def test_nothing_picked_means_a_normal_single_request(self):
        provider = SelectingProvider(picks(), text("ok"))

        self.send(provider, "你好")

        assert self.users(provider, 1) == ["你好"]

    def test_another_sessions_plugins_are_not_candidates(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        other = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        provider = SelectingProvider(picks(), text("ok"))

        with mock.patch("apps.harness.get_provider", return_value=provider):
            self.client.post(f"/api/sessions/{other}/messages/", {"content": "hi"}, format="json")

        assert "mine" not in str(provider.calls[0]["conversation"][0]["content"])

    def test_a_plugin_the_model_invents_is_ignored(self):
        provider = SelectingProvider(picks("no-such-skill"), text("ok"))

        self.send(provider, "你好")

        assert self.users(provider, 1) == ["你好"]

    # --- 对话里怎么留 ---

    def test_the_loaded_skill_is_saved_in_the_conversation_once_and_not_repeated_every_turn(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        provider = SelectingProvider(picks("mine"), text("one"), picks("mine"), text("two"), picks("mine"), text("three"))

        self.send(provider, "第一句")
        self.send(provider, "第二句")
        self.send(provider, "第三句")

        # 第一次加载后留在历史里，之后每轮都带着它，但不会重复加载
        assert sum("MINE-BODY" in m for m in self.users(provider, 5)) == 1
        saved = ChatSession.objects.get(pk=self.sid).conversation
        assert sum("MINE-BODY" in str(m.get("content")) for m in saved) == 1

    def test_a_plugin_added_later_can_be_picked_on_the_next_message(self):
        provider = SelectingProvider(picks(), text("one"), picks("late"), text("two"))
        self.send(provider, "第一句")
        self.add("late.md", skill("late", "后来的", "LATE-BODY"))

        self.send(provider, "第二句")

        assert any("LATE-BODY" in m for m in self.users(provider, 3))

    def test_the_user_visible_message_history_does_not_contain_the_skill_text(self):
        # 加载的是发给模型的上下文，不是聊天记录：页面上看到的消息里不该出现整份插件正文
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))

        self.send(SelectingProvider(picks("mine"), text("ok")), "请处理")

        shown = self.client.get(f"/api/sessions/{self.sid}/messages/").json()
        assert [m["content"] for m in shown if m["role"] == "user"] == ["请处理"]

    # --- 异常情况 ---

    def test_a_plugin_file_without_frontmatter_is_not_a_candidate_and_the_request_still_works(self):
        # 老数据：以前允许上传任何 .md，里面可能有没有 frontmatter 的
        skills_dir = Path(harness.skills_dir(ChatSession.objects.get(pk=self.sid)))
        skills_dir.mkdir(parents=True)
        (skills_dir / "old.md").write_text("no frontmatter", encoding="utf-8")
        provider = SelectingProvider(picks(), text("ok"))

        res = self.send(provider)

        assert "no frontmatter" not in str(provider.calls[0]["conversation"])
        assert res.json()[1]["content"] == "ok"  # 请求没有被一个坏文件拖垮

    def test_a_failing_selection_request_does_not_break_the_reply(self):
        provider = SelectingProvider(RuntimeError("boom"), text("ok"))

        res = self.send(provider, "你好")

        assert res.json()[1]["content"] == "ok"

    def test_a_failed_model_call_leaves_no_trace_of_the_loaded_skill_in_the_saved_conversation(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        failing = SelectingProvider(picks("mine"), RuntimeError("boom"))

        res = self.send(failing)

        assert "调用模型失败" in res.json()[1]["content"]
        assert ChatSession.objects.get(pk=self.sid).conversation == []

    def test_streaming_loads_the_same_way(self):
        self.add("mine.md", skill("mine", "我的", "MINE-BODY"))
        provider = SelectingProvider(picks("mine"), text("ok"))

        with mock.patch("apps.harness.get_provider", return_value=provider):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/stream/", {"content": "请处理"}, format="json")
            events = sse_events(res)

        assert events[-1][0] == "done"
        assert "MINE-BODY" in self.users(provider, 1)[0]

    def test_model_and_effort_pass_through_to_the_formal_request(self):
        provider = SelectingProvider(picks("default-one"), text("ok"))

        self.send(provider, model="deepseek-v4-pro", effort="max")

        assert provider.options[1] == ("deepseek-v4-pro", "max")  # 正式请求用用户选的；第 0 个是选择请求
        assert "DEFAULT-BODY" in self.users(provider, 1)[0]
