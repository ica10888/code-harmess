"""工作流跟着选中的 skill 走：skill_selector 选中某个 skill，同名（frontmatter name）的工作流就把输出格式（schema）注入对话。

覆盖：
  - 选中的 skill 有同名工作流：schema 作为输出要求注入对话，只注入一次；
  - 没选中任何 skill、或选中的 skill 没有同名工作流：不注入；
  - 工作流文件的 schema 坏了：不注入，请求照常；
  - 注入的文字要求模型缺输入时先询问，而不是拒绝。
没有「勾选」：用哪个插件、带哪个工作流，全由模型在每句话之前选。模型用 FakeProvider，不调用真实 API。
"""

import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.test_eureka import GOOD, PLUGIN, RecordingProvider, flow
from apps.test_harness import picks, text


class WorkflowBase(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.skills = self.tmp / "skills_default"
        self.flows = self.tmp / "flows_default"
        self.skills.mkdir()
        self.flows.mkdir()
        (self.skills / "代码审查.md").write_bytes(PLUGIN.format(name="code-review").encode("utf-8"))
        (self.skills / "别的.md").write_bytes(PLUGIN.format(name="other-flow").encode("utf-8"))  # 没有同名工作流
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

    def send(self, provider, content="请审查"):
        with mock.patch("apps.harness.get_provider", return_value=provider):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/", {"content": content}, format="json")
        assert res.status_code == 201, res.content
        return res.json()

    def user_text(self, provider, call):
        return "\n".join(m["content"] for m in provider.calls[call]["conversation"] if m.get("role") == "user")


class WorkflowFollowsSkillTests(WorkflowBase):
    def test_a_selected_skill_brings_its_same_named_workflow_schema_as_the_output_requirement(self):
        provider = RecordingProvider(picks("code-review"), text(GOOD))

        self.send(provider)

        sent = self.user_text(provider, call=1)  # call=0 是「选 skill」那次请求
        assert '<workflow name="code-review">' in sent
        assert '"enum": ["SUGGESTION", "QUESTION"]' in sent  # schema 原文
        assert "REVIEW-RULES" in sent  # skill 全文也在

    def test_the_workflow_is_injected_only_once_per_conversation(self):
        provider = RecordingProvider(picks("code-review"), text(GOOD), picks("code-review"), text(GOOD))
        self.send(provider)
        self.send(provider)

        assert self.user_text(provider, call=3).count('<workflow name="code-review">') == 1

    def test_nothing_is_injected_when_no_skill_is_selected(self):
        provider = RecordingProvider(picks(), text("好的"))

        self.send(provider)

        assert "<workflow" not in self.user_text(provider, call=1)

    def test_a_selected_skill_without_a_same_named_workflow_brings_no_workflow(self):
        provider = RecordingProvider(picks("other-flow"), text("好的"))

        self.send(provider)

        sent = self.user_text(provider, call=1)
        assert "<workflow" not in sent and '<skill name="other-flow">' in sent

    def test_the_injected_text_tells_the_model_to_ask_for_missing_input_instead_of_refusing(self):
        provider = RecordingProvider(picks("code-review"), text(GOOD))
        self.send(provider)

        assert "ask the user" in self.user_text(provider, call=1)

    def test_a_workflow_with_a_broken_schema_is_not_injected_and_the_request_still_works(self):
        bad = '---\nname: code-review\ndescription: d\nschema: {"type": "no-such-type"}\n---\n正文\n'
        (self.flows / "代码审查.md").write_bytes(bad.encode("utf-8"))
        provider = RecordingProvider(picks("code-review"), text("好的"))

        reply = self.send(provider)[1]

        assert "<workflow" not in self.user_text(provider, call=1)
        assert reply["content"] == "好的"

    def test_the_user_visible_history_does_not_contain_the_workflow_text(self):
        self.send(RecordingProvider(picks("code-review"), text(GOOD)))

        history = self.client.get(f"/api/sessions/{self.sid}/messages/").json()

        assert all("<workflow" not in m["content"] for m in history)
