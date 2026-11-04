"""勾选工作流：自动勾选同名插件、只能勾一个、把输出格式（schema）告诉模型，以及各种用户可感知的反馈。

覆盖：
  - 勾选工作流时，同名（frontmatter name）的插件自动勾上；找不到同名插件或工作流文件有问题，勾选照样成功，但响应里带 notice；
  - 同时只能勾一个工作流（再勾另一个返回 409）；取消工作流不动插件；
  - 勾选的工作流的 schema 作为输出要求注入对话，只注入一次；没勾不注入；
  - 勾了工作流但这一轮回复不符合 schema 时，assistant 消息带 notice（没触发 eureka 的原因）。
模型用 FakeProvider，不调用真实 API。
"""

import tempfile
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.test_eureka import GOOD, PLUGIN, REVIEW_SCHEMA, RecordingProvider, flow
from apps.test_harness import text


class WorkflowEnableTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.skills = self.tmp / "skills_default"
        self.flows = self.tmp / "flows_default"
        self.skills.mkdir()
        self.flows.mkdir()
        (self.skills / "代码审查.md").write_bytes(PLUGIN.format(name="code-review").encode("utf-8"))
        (self.flows / "代码审查.md").write_bytes(flow("code-review").encode("utf-8"))
        (self.flows / "别的.md").write_bytes(flow("other-flow").encode("utf-8"))  # 没有同名插件
        override = override_settings(
            HARNESS_STORAGE_DIR=str(self.tmp / "s"),
            HARNESS_DEFAULT_SKILLS_DIR=str(self.skills),
            HARNESS_DEFAULT_WORKFLOW_DIR=str(self.flows),
        )
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]

    def check_workflow(self, name="代码审查.md", enabled=True):
        return self.client.patch(
            f"/api/sessions/{self.sid}/workflows/{quote(name)}/", {"enabled": enabled}, format="json"
        )

    def plugin_enabled(self, name="代码审查.md"):
        plugins = self.client.get(f"/api/sessions/{self.sid}/plugins/").json()
        return next(p["enabled"] for p in plugins if p["name"] == name)

    def send(self, provider, content="请审查"):
        with mock.patch("apps.harness.get_provider", return_value=provider):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/", {"content": content}, format="json")
        assert res.status_code == 201, res.content
        return res.json()

    def user_text(self, provider, call=0):
        return "\n".join(m["content"] for m in provider.calls[call]["conversation"] if m.get("role") == "user")

    # --- 自动勾选同名插件 ---

    def test_checking_a_workflow_also_checks_the_plugin_with_the_same_name(self):
        res = self.check_workflow()

        assert res.status_code == 200
        assert res.json()["enabled"] is True and res.json().get("notice") is None
        assert self.plugin_enabled() is True

    def test_checking_a_workflow_unchecks_all_other_plugins(self):
        (self.skills / "其他.md").write_bytes(PLUGIN.format(name="other-skill").encode("utf-8"))
        self.client.patch(f"/api/sessions/{self.sid}/plugins/{quote('其他.md')}/", {"enabled": True}, format="json")

        self.check_workflow()

        assert self.plugin_enabled() is True
        assert self.plugin_enabled("其他.md") is False

    def test_other_plugins_are_kept_when_there_is_no_same_named_plugin(self):
        (self.skills / "其他.md").write_bytes(PLUGIN.format(name="other-skill").encode("utf-8"))
        self.client.patch(f"/api/sessions/{self.sid}/plugins/{quote('其他.md')}/", {"enabled": True}, format="json")

        self.check_workflow("别的.md")

        assert self.plugin_enabled("其他.md") is True

    def test_checking_a_workflow_without_a_same_named_plugin_succeeds_with_a_notice(self):
        res = self.check_workflow("别的.md")

        assert res.status_code == 200 and res.json()["enabled"] is True
        assert "other-flow" in res.json()["notice"] and "插件" in res.json()["notice"]

    def test_unchecking_a_workflow_leaves_the_plugin_checked(self):
        self.check_workflow()
        res = self.check_workflow(enabled=False)

        assert res.json()["enabled"] is False
        assert self.plugin_enabled() is True

    def test_a_workflow_file_that_cannot_be_read_still_checks_but_warns(self):
        (self.flows / "坏.md").write_bytes(b"no frontmatter here")

        res = self.check_workflow("坏.md")

        assert res.status_code == 200 and res.json()["enabled"] is True
        assert "格式" in res.json()["notice"]

    # --- 只能勾一个 ---

    def test_only_one_workflow_can_be_checked_at_a_time(self):
        self.check_workflow()

        res = self.check_workflow("别的.md")

        assert res.status_code == 409
        assert "代码审查.md" in res.json()["detail"]
        flows = {w["name"]: w["enabled"] for w in self.client.get(f"/api/sessions/{self.sid}/workflows/").json()}
        assert flows == {"代码审查.md": True, "别的.md": False}

    def test_after_unchecking_another_workflow_can_be_checked(self):
        self.check_workflow()
        self.check_workflow(enabled=False)

        assert self.check_workflow("别的.md").status_code == 200

    def test_checking_the_already_checked_workflow_again_is_fine(self):
        self.check_workflow()
        assert self.check_workflow().status_code == 200

    # --- 把输出要求告诉模型 ---

    def test_a_checked_workflow_schema_is_injected_as_the_output_requirement(self):
        self.check_workflow()
        provider = RecordingProvider(text(GOOD))

        self.send(provider)

        sent = self.user_text(provider)
        assert '<workflow name="code-review">' in sent
        assert '"enum": ["SUGGESTION", "QUESTION"]' in sent  # schema 原文
        assert "REVIEW-RULES" in sent  # 同名插件也跟着注入了

    def test_the_workflow_is_injected_only_once_per_conversation(self):
        self.check_workflow()
        provider = RecordingProvider(text(GOOD), text(GOOD))
        self.send(provider)
        self.send(provider)

        assert self.user_text(provider, call=1).count('<workflow name="code-review">') == 1

    def test_nothing_is_injected_when_no_workflow_is_checked(self):
        provider = RecordingProvider(text("好的"))

        self.send(provider)

        assert "<workflow" not in self.user_text(provider)

    def test_the_injected_text_tells_the_model_to_ask_for_missing_input_instead_of_refusing(self):
        self.check_workflow()
        provider = RecordingProvider(text(GOOD))
        self.send(provider)

        assert "ask the user" in self.user_text(provider)

    # --- 回复不符合格式时的反馈 ---

    def test_a_reply_that_fails_the_schema_gets_a_notice_when_a_workflow_is_checked(self):
        self.check_workflow()

        assistant = self.send(RecordingProvider(text("我没有看到代码")))[1]

        assert assistant["eureka"] is None
        assert "格式" in assistant["notice"] and "code-review" in assistant["notice"]

    def test_a_passing_reply_has_no_notice(self):
        self.check_workflow()

        assistant = self.send(RecordingProvider(text(GOOD)))[1]

        assert assistant["eureka"] is not None and assistant["notice"] is None

    def test_no_notice_when_no_workflow_is_checked(self):
        assistant = self.send(RecordingProvider(text("随便聊聊")))[1]

        assert assistant["notice"] is None

    def test_the_notice_survives_a_page_refresh(self):
        self.check_workflow()
        self.send(RecordingProvider(text("我没有看到代码")))

        history = self.client.get(f"/api/sessions/{self.sid}/messages/").json()

        assert "格式" in history[-1]["notice"]
