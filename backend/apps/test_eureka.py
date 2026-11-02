"""eureka 时刻：用了插件、且回复通过了同名工作流的 schema 时，在对话框里多一条提示。

判定（workflows.match_eureka）：
  - 这一轮注入了插件 X（勾选的）；
  - 工作流里有 name 也是 X 的文件（默认工作流 + 这个会话自己的，不要求勾选）；
  - 模型这一轮的回复里能取出 JSON，并且通过那个工作流 frontmatter 里的 schema 校验。
提示文本来自 config.ini（[eureka] text）；保存在 SQL 表 Eureka，关联到那条 assistant 消息，不进模型上下文。
真正的「eureka 之后做什么」以后再做，这里只管判定、保存和展示。
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps import harness, workflows
from apps.models import ChatSession, Eureka, Message
from apps.test_harness import FakeProvider, sse_events, text

REVIEW_SCHEMA = """{
  "type": "array",
  "items": {
    "type": "object",
    "properties": {"line": {"type": "integer"}, "type": {"type": "string", "enum": ["SUGGESTION", "QUESTION"]}},
    "required": ["line", "type"],
    "additionalProperties": false
  }
}"""

PLUGIN = "---\nname: {name}\ndescription: 审查代码\n---\n\n审查规则 REVIEW-RULES\n"
FLOW = "---\nname: {name}\ndescription: 审查工作流\nschema: |\n{schema}\n---\n\n# 工作流\n\n后续步骤。\n"


def indent(schema):
    return "\n".join("  " + line for line in schema.splitlines())


def flow(name="code-review", schema=REVIEW_SCHEMA):
    return FLOW.format(name=name, schema=indent(schema))


GOOD = '[{"line": 3, "type": "SUGGESTION"}]'


class MatchEurekaTests(TestCase):
    """workflows.match_eureka：纯判定，不碰数据库。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.defaults = self.tmp / "default_workflow"
        self.defaults.mkdir()
        override = override_settings(
            HARNESS_STORAGE_DIR=str(self.tmp / "s"), HARNESS_DEFAULT_WORKFLOW_DIR=str(self.defaults)
        )
        override.enable()
        self.addCleanup(override.disable)
        self.session = ChatSession.objects.create(owner_key="k")

    def put_default(self, filename, name="code-review", schema=REVIEW_SCHEMA):
        (self.defaults / filename).write_bytes(flow(name, schema).encode("utf-8"))

    def put_own(self, filename, name="code-review", schema=REVIEW_SCHEMA):
        root = Path(harness.workflow_dir(self.session))
        root.mkdir(parents=True, exist_ok=True)
        (root / filename).write_bytes(flow(name, schema).encode("utf-8"))

    # --- 命中 ---

    def test_matches_when_a_used_plugin_has_a_same_named_workflow_and_the_reply_passes_its_schema(self):
        self.put_default("代码审查.md")

        hit = workflows.match_eureka(self.session, ["code-review"], GOOD)

        assert {k: hit[k] for k in ("plugin", "workflow")} == {"plugin": "code-review", "workflow": "代码审查.md"}
        assert hit["payload"] == [{"line": 3, "type": "SUGGESTION"}]  # 通过校验的那份 JSON 一并返回，工作流要用它

    def test_the_workflow_does_not_need_to_be_checked(self):
        self.put_default("代码审查.md")  # 没有任何勾选状态
        assert workflows.match_eureka(self.session, ["code-review"], GOOD) is not None

    def test_a_workflow_in_the_sessions_own_directory_counts_too(self):
        self.put_own("my-review.md")
        assert workflows.match_eureka(self.session, ["code-review"], GOOD)["workflow"] == "my-review.md"

    def test_the_match_is_by_frontmatter_name_not_by_file_name(self):
        self.put_default("完全不同的文件名.md", name="code-review")
        assert workflows.match_eureka(self.session, ["code-review"], GOOD)["workflow"] == "完全不同的文件名.md"

    def test_any_of_several_used_plugins_can_trigger_it(self):
        self.put_default("a.md", name="analyze-config", schema='{"type": "array"}')
        hit = workflows.match_eureka(self.session, ["code-review", "analyze-config"], "[]")
        assert hit["plugin"] == "analyze-config"

    # --- 不命中 ---

    def test_no_used_plugin_means_no_eureka_even_if_the_reply_fits_a_workflow(self):
        self.put_default("代码审查.md")
        assert workflows.match_eureka(self.session, [], GOOD) is None

    def test_a_used_plugin_with_no_same_named_workflow_means_no_eureka(self):
        self.put_default("代码审查.md", name="something-else")
        assert workflows.match_eureka(self.session, ["code-review"], GOOD) is None

    def test_a_reply_that_fails_the_schema_means_no_eureka(self):
        self.put_default("代码审查.md")
        for bad in ['[{"line": "three", "type": "SUGGESTION"}]', '[{"line": 3, "type": "OTHER"}]', '[{"line": 3}]', '{"line": 3}', "[1]"]:
            assert workflows.match_eureka(self.session, ["code-review"], bad) is None, bad

    def test_extra_properties_are_rejected_when_the_schema_forbids_them(self):
        self.put_default("代码审查.md")
        assert workflows.match_eureka(self.session, ["code-review"], '[{"line": 1, "type": "QUESTION", "x": 1}]') is None

    def test_a_reply_with_no_json_means_no_eureka(self):
        self.put_default("代码审查.md")
        for none in ["", "没问题。", "我觉得还行 [注意]", None]:
            assert workflows.match_eureka(self.session, ["code-review"], none) is None, none

    # --- 从回复里取 JSON ---

    def test_json_wrapped_in_a_code_fence_is_found(self):
        self.put_default("代码审查.md")
        reply = f"审查结果如下：\n```json\n{GOOD}\n```\n以上。"
        assert workflows.match_eureka(self.session, ["code-review"], reply) is not None

    def test_a_fence_without_a_language_tag_is_found(self):
        self.put_default("代码审查.md")
        assert workflows.match_eureka(self.session, ["code-review"], f"```\n{GOOD}\n```") is not None

    def test_json_surrounded_by_prose_is_found(self):
        self.put_default("代码审查.md")
        assert workflows.match_eureka(self.session, ["code-review"], f"好的，结果：{GOOD} 请查收。") is not None

    def test_chinese_and_nested_brackets_inside_strings_do_not_confuse_the_extraction(self):
        self.put_default("a.md", name="x", schema='{"type": "array", "items": {"type": "string"}}')
        assert workflows.match_eureka(self.session, ["x"], '前言 ["含 ] 括号 和 } 花括号 的字符串", "第二项"] 后语') is not None

    def test_if_several_json_values_appear_any_that_passes_counts(self):
        self.put_default("代码审查.md")
        reply = f'先看一个例子 {{"example": true}}，然后是正式结果：\n```json\n{GOOD}\n```'
        assert workflows.match_eureka(self.session, ["code-review"], reply) is not None

    # --- 容错：坏的工作流文件不能拖垮判定 ---

    def test_a_workflow_with_a_broken_schema_is_skipped(self):
        (self.defaults / "bad.md").write_bytes(b"---\nname: code-review\ndescription: d\nschema: not json\n---\n")
        assert workflows.match_eureka(self.session, ["code-review"], GOOD) is None

    def test_a_workflow_with_an_invalid_schema_document_is_skipped_not_a_crash(self):
        self.put_default("bad.md", schema='{"type": "no-such-type"}')
        assert workflows.match_eureka(self.session, ["code-review"], GOOD) is None

    def test_a_workflow_file_without_frontmatter_is_skipped(self):
        (self.defaults / "plain.md").write_text("just text", encoding="utf-8")
        assert workflows.match_eureka(self.session, ["code-review"], GOOD) is None

    def test_a_bad_workflow_does_not_hide_a_good_one(self):
        (self.defaults / "a-bad.md").write_bytes(b"---\nname: code-review\ndescription: d\nschema: nope\n---\n")
        self.put_default("b-good.md")
        assert workflows.match_eureka(self.session, ["code-review"], GOOD)["workflow"] == "b-good.md"

    def test_no_workflow_directories_at_all_is_just_no_eureka(self):
        with override_settings(HARNESS_DEFAULT_WORKFLOW_DIR=""):
            assert workflows.match_eureka(self.session, ["code-review"], GOOD) is None

    def test_a_huge_reply_does_not_blow_up_and_a_json_buried_in_it_is_still_found(self):
        self.put_default("代码审查.md")
        assert workflows.match_eureka(self.session, ["code-review"], "x" * 2_000_000 + GOOD) is not None


class EurekaTextConfigTests(TestCase):
    def test_the_text_comes_from_settings(self):
        with override_settings(EUREKA_TEXT="自定义文本"):
            assert workflows.eureka_text() == "自定义文本"

    def test_the_default_text_is_eye_catching_and_has_emoji(self):
        from django.conf import settings

        shown = settings.EUREKA_TEXT
        assert shown.strip() and "Eureka" in shown or "eureka" in shown.lower() or "尤里卡" in shown
        assert any(ord(ch) > 0x2000 for ch in shown)  # 有 emoji / 符号：辨识度高

    def test_missing_config_falls_back_to_a_built_in_text(self):
        with override_settings(EUREKA_TEXT=""):
            assert workflows.eureka_text().strip()


class RecordingProvider(FakeProvider):
    def chat(self, conversation, tools, system="", on_text=None, model=None, effort=None):
        return super().chat(conversation, tools, system, on_text)


class EurekaFlowTests(TestCase):
    """整条链路：勾选插件 → 发消息 → 回复通过同名工作流的 schema → 存一条 Eureka、接口带出来、不进上下文。"""

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
        override = override_settings(
            HARNESS_STORAGE_DIR=str(self.tmp / "s"),
            HARNESS_DEFAULT_SKILLS_DIR=str(self.skills),
            HARNESS_DEFAULT_WORKFLOW_DIR=str(self.flows),
            EUREKA_TEXT="🎉 Eureka！测试文本",
        )
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]

    def check_plugin(self, name="代码审查.md", enabled=True):
        from urllib.parse import quote

        assert self.client.patch(f"/api/sessions/{self.sid}/plugins/{quote(name)}/", {"enabled": enabled}, format="json").status_code == 200

    def send(self, reply, content="请审查"):
        with mock.patch("apps.harness.get_provider", return_value=RecordingProvider(text(reply))):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/", {"content": content}, format="json")
        assert res.status_code == 201, res.content
        return res.json()

    def history(self):
        return self.client.get(f"/api/sessions/{self.sid}/messages/").json()

    # --- 触发 ---

    def test_a_passing_reply_with_a_used_plugin_produces_an_eureka_on_the_assistant_message(self):
        self.check_plugin()

        user, assistant = self.send(GOOD)

        assert assistant["eureka"]["text"] == "🎉 Eureka！测试文本"
        assert user.get("eureka") is None  # 只挂在 assistant 消息上
        assert Eureka.objects.count() == 1

    def test_the_eureka_row_remembers_which_plugin_and_workflow_matched(self):
        self.check_plugin()
        self.send(GOOD)

        row = Eureka.objects.get()

        assert (row.plugin, row.workflow) == ("code-review", "代码审查.md")
        assert row.message.role == "assistant" and str(row.message.session_id) == self.sid
        assert row.text == "🎉 Eureka！测试文本"  # 存的是当时展示的文本：以后改了配置，老记录不变

    def test_no_eureka_when_the_plugin_is_not_checked(self):
        assert self.send(GOOD)[1]["eureka"] is None
        assert Eureka.objects.count() == 0

    def test_no_eureka_when_the_reply_does_not_pass_the_schema(self):
        self.check_plugin()
        assert self.send('[{"line": "x"}]')[1]["eureka"] is None
        assert Eureka.objects.count() == 0

    def test_no_eureka_when_there_is_no_same_named_workflow(self):
        (self.flows / "代码审查.md").write_bytes(flow("another-name").encode("utf-8"))
        self.check_plugin()
        assert self.send(GOOD)[1]["eureka"] is None

    def test_a_failed_model_call_never_produces_an_eureka(self):
        self.check_plugin()
        failing = RecordingProvider(RuntimeError("boom"))
        with mock.patch("apps.harness.get_provider", return_value=failing):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/", {"content": "x"}, format="json").json()
        assert res[1]["eureka"] is None and Eureka.objects.count() == 0

    # --- 刷新后仍在 ---

    def test_the_eureka_is_still_there_when_the_history_is_loaded_again(self):
        self.check_plugin()
        self.send(GOOD)

        shown = self.history()

        assert [m["eureka"] and m["eureka"]["text"] for m in shown] == [None, "🎉 Eureka！测试文本"]

    def test_only_the_messages_that_triggered_it_carry_one(self):
        self.check_plugin()
        self.send(GOOD, "第一句")
        self.send("没有 JSON", "第二句")
        self.send(GOOD, "第三句")

        assert [bool(m["eureka"]) for m in self.history() if m["role"] == "assistant"] == [True, False, True]

    # --- 不进上下文 ---

    def test_the_eureka_text_is_never_part_of_the_conversation_sent_to_the_model(self):
        self.check_plugin()
        provider = RecordingProvider(text(GOOD), text("好的"))
        with mock.patch("apps.harness.get_provider", return_value=provider):
            self.client.post(f"/api/sessions/{self.sid}/messages/", {"content": "第一句"}, format="json")
            self.client.post(f"/api/sessions/{self.sid}/messages/", {"content": "第二句"}, format="json")

        for call in provider.calls:
            assert "Eureka" not in json.dumps(call["conversation"], ensure_ascii=False)
        assert "Eureka" not in json.dumps(ChatSession.objects.get(pk=self.sid).conversation, ensure_ascii=False)

    def test_the_eureka_text_is_not_added_to_the_message_content_either(self):
        self.check_plugin()
        assistant = self.send(GOOD)[1]
        assert "Eureka" not in assistant["content"]

    # --- 保存在 SQL 里 ---

    def test_it_lives_in_its_own_table_with_a_foreign_key_to_the_message(self):
        self.check_plugin()
        self.send(GOOD)

        with connection.cursor() as cursor:
            cursor.execute("SELECT message_id, plugin, workflow, text FROM apps_eureka")
            rows = cursor.fetchall()

        assert len(rows) == 1 and rows[0][1:] == ("code-review", "代码审查.md", "🎉 Eureka！测试文本")
        assert rows[0][0] == Message.objects.filter(role="assistant").get().id

    def test_deleting_the_message_deletes_its_eureka(self):
        self.check_plugin()
        self.send(GOOD)
        Message.objects.filter(role="assistant").delete()
        assert Eureka.objects.count() == 0

    def test_one_message_has_at_most_one_eureka(self):
        self.check_plugin()
        self.send(GOOD)
        message = Message.objects.filter(role="assistant").get()
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            Eureka.objects.create(message=message, plugin="x", workflow="y", text="z")

    # --- 流式 ---

    def test_streaming_sends_the_eureka_in_the_done_event_and_it_is_saved(self):
        self.check_plugin()
        with mock.patch("apps.harness.get_provider", return_value=RecordingProvider(text(GOOD))):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/stream/", {"content": "请审查"}, format="json")
            events = sse_events(res)

        kind, done = events[-1]
        assert kind == "done" and done["eureka"]["text"] == "🎉 Eureka！测试文本"
        assert Eureka.objects.count() == 1

    def test_streaming_without_a_match_has_a_null_eureka(self):
        with mock.patch("apps.harness.get_provider", return_value=RecordingProvider(text("没有 JSON"))):
            res = self.client.post(f"/api/sessions/{self.sid}/messages/stream/", {"content": "x"}, format="json")
            events = sse_events(res)
        assert events[-1][1]["eureka"] is None

    # --- 稳健性 ---

    def test_a_workflow_problem_never_breaks_the_reply(self):
        self.check_plugin()
        with mock.patch("apps.workflows.match_eureka", side_effect=RuntimeError("boom")):
            user, assistant = self.send(GOOD)
        assert assistant["content"] == GOOD and assistant["eureka"] is None  # 回复照常，只是没有 eureka

    def test_the_plugin_used_is_a_checked_one_that_core_can_actually_read(self):
        # 勾选了、但文件已被手动删掉的插件，不算「使用了插件」
        self.check_plugin()
        (self.skills / "代码审查.md").unlink()
        assert self.send(GOOD)[1]["eureka"] is None

    def test_a_plugin_injected_in_an_earlier_turn_still_counts_as_used_in_later_turns(self):
        # 插件全文第一次注入后一直留在对话里，之后每一轮模型都在用它，所以后面的回复也该触发
        self.check_plugin()
        self.send("第一轮没有 JSON", "第一句")

        second = self.send(GOOD, "第二句")

        assert second[1]["eureka"]["text"] == "🎉 Eureka！测试文本"


class FenceRuleTests(TestCase):
    """规则：回复里如果有 ``` 包裹，先去掉包裹，只校验里面的内容；没有包裹才从整段文字里找 JSON。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.defaults = Path(tmp.name) / "wf"
        self.defaults.mkdir()
        (self.defaults / "r.md").write_bytes(flow("code-review").encode("utf-8"))
        override = override_settings(HARNESS_STORAGE_DIR=str(Path(tmp.name) / "s"), HARNESS_DEFAULT_WORKFLOW_DIR=str(self.defaults))
        override.enable()
        self.addCleanup(override.disable)
        self.session = ChatSession.objects.create(owner_key="k")

    def hit(self, reply):
        return workflows.match_eureka(self.session, ["code-review"], reply) is not None

    # --- 去掉包裹，再校验里面的内容 ---

    def test_the_standard_json_fence_is_stripped_and_its_content_validated(self):
        assert self.hit(f"```json\n{GOOD}\n```")

    def test_language_tag_is_case_insensitive_and_optional(self):
        for tag in ["json", "JSON", "Json", "jsonc", ""]:
            assert self.hit(f"```{tag}\n{GOOD}\n```"), tag

    def test_windows_line_endings_inside_the_fence_are_fine(self):
        assert self.hit(f"```json\r\n{GOOD}\r\n```")

    def test_a_pretty_printed_multi_line_json_inside_the_fence_is_fine(self):
        body = '[\n  {\n    "line": 3,\n    "type": "QUESTION"\n  }\n]'
        assert self.hit(f"```json\n{body}\n```")

    def test_surrounding_whitespace_and_blank_lines_inside_the_fence_are_ignored(self):
        assert self.hit(f"```json\n\n   {GOOD}   \n\n```")

    def test_prose_before_and_after_the_fence_is_ignored(self):
        assert self.hit(f"审查结果如下：\n\n```json\n{GOOD}\n```\n\n以上，请查收。")

    def test_a_fence_that_is_the_whole_reply_works(self):
        assert self.hit(f"```json\n{GOOD}\n```")

    def test_four_backticks_and_tilde_fences_are_stripped_too(self):
        assert self.hit(f"````json\n{GOOD}\n````")
        assert self.hit(f"~~~json\n{GOOD}\n~~~")

    def test_a_json_that_contains_backticks_inside_a_string_is_not_cut_short(self):
        schema_free = '{"type": "array", "items": {"type": "string"}}'
        (self.defaults / "r.md").write_bytes(flow("code-review", schema_free).encode("utf-8"))
        assert self.hit('```json\n["含 ``` 三个反引号的字符串"]\n```')

    # --- 包裹里的内容才是校验对象 ---

    def test_when_the_fenced_content_fails_the_schema_it_is_not_rescued_by_json_outside_the_fence(self):
        # 围栏里的才是正式结果；围栏外面碰巧有一段能通过的 JSON，不能拿来凑数
        assert not self.hit(f'```json\n[{{"line": "x"}}]\n```\n另外一个例子：{GOOD}')

    def test_when_the_fenced_content_is_broken_json_it_fails_instead_of_falling_back_to_the_prose(self):
        assert not self.hit(f'```json\n[{{"line": 3, "type": "QUESTION"}},]\n```\n{GOOD}')

    def test_if_there_are_several_fences_any_one_that_passes_counts(self):
        assert self.hit(f'```json\n{{"example": true}}\n```\n正式结果：\n```json\n{GOOD}\n```')

    def test_a_fence_of_another_language_is_still_stripped_and_checked_as_json(self):
        assert self.hit(f"```python\n{GOOD}\n```")  # 语言标记只是装饰，包裹里是不是 JSON 才是关键

    # --- 没有包裹时的行为不变 ---

    def test_without_a_fence_json_inside_prose_is_still_found(self):
        assert self.hit(f"好的，结果：{GOOD} 请查收。")

    def test_without_a_fence_a_bare_json_reply_is_found(self):
        assert self.hit(GOOD)

    def test_an_unclosed_fence_falls_back_to_looking_in_the_text(self):
        # 模型被截断时可能只有开头的 ```json，没有结尾：没有完整的包裹，就当没有包裹
        assert self.hit(f"```json\n{GOOD}")

    def test_nothing_that_looks_like_json_means_no_match(self):
        for reply in ["", "```json\n```", "```\n\n```", "没有结果", "```json\n不是 JSON\n```"]:
            assert not self.hit(reply), reply

    # --- 每个值只校验一次 ---

    def test_each_json_value_is_produced_only_once(self):
        values = list(workflows._json_candidates(f"```json\n{GOOD}\n```"))
        assert values == [[{"line": 3, "type": "SUGGESTION"}]]  # 不是 [x, x]：围栏里的不再被「扫整段文字」重复取一次

    def test_the_order_is_fenced_blocks_first_in_reply_order(self):
        reply = '```json\n{"n": 1}\n```\n中间\n```json\n{"n": 2}\n```'
        assert list(workflows._json_candidates(reply)) == [{"n": 1}, {"n": 2}]
