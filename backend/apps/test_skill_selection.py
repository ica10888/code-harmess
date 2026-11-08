"""选中 skill 的提示：skill_selector 这一轮选中了 skill，就在那条回复下面多一条「🧩 已为你选用插件：XXX，并启用工作流：XXX 🚀」。

结构和 FileChange（文件变更提示）、Eureka 完全一致：单独一张表、挂在 assistant 消息上、不进模型上下文、
序列化器带出来、刷新页面后还在。插件 = 选中的 skill；工作流 = 其中有同名（frontmatter name）工作流的那些。
模型用 FakeProvider，不调用真实 API。
"""

import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.models import SkillSelection
from apps.test_eureka import GOOD, PLUGIN, flow
from apps.test_harness import FakeProvider, picks, sse_events, text


class SelectingProvider(FakeProvider):
    def chat(self, conversation, tools, system="", on_text=None, model=None, effort=None):
        return super().chat(conversation, tools, system, on_text)


class SkillSelectionTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.skills, self.flows = self.tmp / "skills", self.tmp / "flows"
        self.skills.mkdir()
        self.flows.mkdir()
        (self.skills / "代码审查.md").write_bytes(PLUGIN.format(name="code-review").encode("utf-8"))
        (self.skills / "别的.md").write_bytes(PLUGIN.format(name="other-skill").encode("utf-8"))  # 没有同名工作流
        (self.flows / "代码审查.md").write_bytes(flow("code-review").encode("utf-8"))
        override = override_settings(
            HARNESS_STORAGE_DIR=str(self.tmp / "s"),
            HARNESS_DEFAULT_SKILLS_DIR=str(self.skills),
            HARNESS_DEFAULT_WORKFLOW_DIR=str(self.flows),
        )
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]

    def send(self, *replies, content="请审查"):
        with mock.patch("apps.harness.get_provider", return_value=SelectingProvider(*replies)):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/", {"content": content}, format="json")
        assert res.status_code == 201, res.content
        return res.json()

    def test_a_selected_skill_with_a_same_named_workflow_shows_both(self):
        assistant = self.send(picks("code-review"), text(GOOD))[1]

        selection = assistant["skill_selection"]
        assert selection["plugins"] == ["code-review"] and selection["workflows"] == ["code-review"]
        assert "code-review" in selection["text"] and "插件" in selection["text"] and "工作流" in selection["text"]
        assert SkillSelection.objects.count() == 1

    def test_several_selected_skills_are_joined_with_he(self):
        assistant = self.send(picks("code-review", "other-skill"), text("好的"))[1]

        selection = assistant["skill_selection"]
        assert selection["plugins"] == ["code-review", "other-skill"]
        assert selection["workflows"] == ["code-review"]  # other-skill 没有同名工作流
        assert selection["text"] == "🧩 已为你选用插件：code-review 和 other-skill，并启用工作流：code-review 🚀"

    def test_a_selected_skill_without_a_workflow_only_mentions_the_plugin(self):
        assistant = self.send(picks("other-skill"), text("好的"))[1]

        selection = assistant["skill_selection"]
        assert selection["workflows"] == []
        assert selection["text"] == "🧩 已为你选用插件：other-skill"

    def test_nothing_selected_means_no_hint(self):
        assistant = self.send(picks(), text("好的"))[1]

        assert assistant["skill_selection"] is None
        assert SkillSelection.objects.count() == 0

    def test_a_skill_already_loaded_earlier_is_still_reported_when_it_is_selected_again(self):
        self.send(picks("code-review"), text(GOOD))
        assistant = self.send(picks("code-review"), text(GOOD), content="再来一次")[1]

        assert assistant["skill_selection"]["plugins"] == ["code-review"]  # 这一轮选中了，哪怕全文不再重复放

    def test_the_hint_is_not_part_of_the_model_context(self):
        self.send(picks("code-review"), text(GOOD))

        from apps.models import ChatSession

        conversation = ChatSession.objects.get(pk=self.sid).conversation
        assert all("已为你选用插件" not in str(m.get("content")) for m in conversation)

    def test_the_hint_survives_a_page_refresh(self):
        self.send(picks("code-review"), text(GOOD))

        history = self.client.get(f"/api/sessions/{self.sid}/messages/").json()

        assert history[-1]["skill_selection"]["plugins"] == ["code-review"]
        assert history[0]["skill_selection"] is None  # 用户那条没有

    def test_streaming_sends_the_hint_in_the_done_event(self):
        with mock.patch("apps.harness.get_provider", return_value=SelectingProvider(picks("code-review"), text(GOOD))):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/stream/", {"content": "请审查"}, format="json")
            events = sse_events(res)

        kind, done = events[-1]
        assert kind == "done" and done["skill_selection"]["plugins"] == ["code-review"]
        assert SkillSelection.objects.count() == 1

    def test_a_failure_while_saving_the_hint_does_not_break_the_reply(self):
        with mock.patch("apps.services.SkillSelection.objects.create", side_effect=RuntimeError("boom")):
            assistant = self.send(picks("code-review"), text(GOOD))[1]

        assert assistant["content"] == GOOD
        assert assistant["skill_selection"] is None

    def test_the_hint_does_not_leak_into_the_next_turn(self):
        self.send(picks("code-review"), text(GOOD))
        assistant = self.send(picks(), text("好的"), content="随便聊聊")[1]

        assert assistant["skill_selection"] is None
