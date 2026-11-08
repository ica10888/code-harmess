"""模型和推理强度：后端枚举、枚举接口、发送时的透传。

枚举的值是写死的（取自 DeepSeek 官方文档，见 providers.MODELS / EFFORTS），前端只负责展示和选择。
"""

import tempfile
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.models import ChatSession
from apps.options import Effort, Model
from apps.test_harness import FakeProvider, sse_events, text
from providers import EFFORTS, MODELS


class EnumTests(TestCase):
    def test_enum_values_match_what_deepseek_and_core_accept(self):
        assert [m.value for m in Model] == list(MODELS)
        assert [e.value for e in Effort] == list(EFFORTS)

    def test_each_choice_has_a_chinese_label_for_the_page(self):
        assert all(m.label for m in Model) and all(e.label for e in Effort)


class OptionsApiTests(TestCase):
    def test_lists_models_and_efforts_with_value_label_and_defaults(self):
        res = APIClient().get("/api/options/")

        assert res.status_code == 200
        data = res.json()
        assert [m["value"] for m in data["models"]] == list(MODELS)
        assert [e["value"] for e in data["efforts"]] == list(EFFORTS)
        assert all(m["label"] for m in data["models"]) and all(e["label"] for e in data["efforts"])
        assert data["default_model"] in MODELS and data["default_effort"] in EFFORTS

    def test_efforts_are_ordered_from_weakest_to_strongest_for_the_slider(self):
        efforts = [e["value"] for e in APIClient().get("/api/options/").json()["efforts"]]
        assert efforts == ["none", "low", "high", "max"]

    def test_default_model_follows_the_config(self):
        with override_settings(DEEPSEEK_MODEL="deepseek-v4-pro"):
            assert APIClient().get("/api/options/").json()["default_model"] == "deepseek-v4-pro"

    def test_needs_no_session_and_only_allows_get(self):
        assert APIClient().post("/api/options/", {}, format="json").status_code == 405


class RecordingProvider(FakeProvider):
    """FakeProvider + 记下 model / effort。"""

    def chat(self, conversation, tools, system="", on_text=None, model=None, effort=None):
        self.options = [*getattr(self, "options", []), {"model": model, "effort": effort}]
        return super().chat(conversation, tools, system, on_text)


class SendOptionsTests(TestCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        override = override_settings(HARNESS_STORAGE_DIR=storage.name, HARNESS_DEFAULT_SKILLS_DIR="")
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]

    def post(self, url, body, provider):
        with mock.patch("apps.harness.get_provider", return_value=provider):
            res = self.client.post(f"/api/sessions/{self.sid}/{url}", body, format="json")
            if res["Content-Type"].startswith("text/event-stream"):
                res.events = sse_events(res)
            return res

    def test_stream_endpoint_passes_model_and_effort_through_to_the_provider(self):
        provider = RecordingProvider(text("ok"))

        res = self.post("messages/stream/", {"content": "hi", "model": "deepseek-v4-pro", "effort": "max"}, provider)

        assert res.status_code == 200 and res.events[-1][0] == "done"
        assert provider.options == [{"model": "deepseek-v4-pro", "effort": "max"}]

    def test_plain_endpoint_passes_them_through_too(self):
        provider = RecordingProvider(text("ok"))

        res = self.post("messages/", {"content": "hi", "model": "deepseek-flash", "effort": "none"}, provider)

        assert res.status_code == 201
        assert provider.options == [{"model": "deepseek-flash", "effort": "none"}]

    def test_both_fields_are_optional_and_the_defaults_then_come_from_the_provider(self):
        provider = RecordingProvider(text("ok"))

        res = self.post("messages/stream/", {"content": "hi"}, provider)

        assert res.status_code == 200
        assert provider.options == [{"model": None, "effort": None}]

    def test_one_field_alone_is_fine(self):
        provider = RecordingProvider(text("ok"))
        self.post("messages/stream/", {"content": "hi", "effort": "high"}, provider)
        assert provider.options == [{"model": None, "effort": "high"}]

    def test_unknown_model_or_effort_is_a_400_before_anything_is_stored_or_sent(self):
        for bad in ({"model": "gpt-4"}, {"effort": "medium"}, {"model": ""}, {"effort": ""}, {"model": "deepseek-v4-flash"}):
            provider = RecordingProvider(text("ok"))
            res = self.post("messages/stream/", {"content": "hi", **bad}, provider)
            assert res.status_code == 400, bad
            assert not hasattr(provider, "options"), bad  # 模型一次都没被调用
        assert ChatSession.objects.get(pk=self.sid).messages.count() == 0  # 连用户消息都没存

    def test_the_choice_applies_to_every_round_of_the_tool_loop(self):
        from providers import ToolCall

        from apps.test_harness import calls

        provider = RecordingProvider(calls(ToolCall("c1", "list_files", {})), text("好了"))

        self.post("messages/stream/", {"content": "hi", "model": "deepseek-v4-pro", "effort": "low"}, provider)

        assert provider.options == [{"model": "deepseek-v4-pro", "effort": "low"}] * 2

    def test_the_choice_is_per_request_and_not_remembered_for_the_next_one(self):
        provider = RecordingProvider(text("a"), text("b"))
        self.post("messages/stream/", {"content": "1", "model": "deepseek-v4-pro", "effort": "max"}, provider)
        self.post("messages/stream/", {"content": "2"}, provider)
        assert provider.options[1] == {"model": None, "effort": None}

    def test_the_system_prompt_names_the_model_that_is_actually_answering(self):
        provider = RecordingProvider(text("ok"))
        self.post("messages/stream/", {"content": "hi", "model": "deepseek-v4-pro"}, provider)
        assert "deepseek-v4-pro" in provider.calls[0]["system"]
