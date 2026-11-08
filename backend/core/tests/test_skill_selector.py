"""skill_selector：正式请求之前，先问一次模型「这次要加载哪些 skill」，只返回 SkillRef(name, path)。

请求按 docs/codex_skill_loading_report_v5.md 的伪请求排 input：
  ① 目录：role=developer，<skills_instructions> 包着，每个 skill 一行；
  ② 用户输入：role=user；
  ③ 用户用 $名字 点名的 skill 正文：role=user，<skill> 包着，写在用户消息之后。
"""

from providers import Reply
from skill_selector import SkillRef, build_selection_input, select_skills
from skills import Skill


class FakeProvider:
    label = "Fake"

    def __init__(self, text):
        self.text = text
        self.calls: list[dict] = []

    def chat(self, conversation, tools, system="", on_text=None, model=None, effort=None):
        self.calls.append({"input": list(conversation), "tools": tools, "system": system, "effort": effort})
        return Reply(texts=[self.text])


def make_skills(tmp_path):
    out = []
    for name, desc in [("commit-msg", "生成提交信息"), ("code-review", "审查代码")]:
        path = tmp_path / f"{name}.md"
        path.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{name.upper()}-BODY\n", encoding="utf-8")
        out.append(Skill(name=name, description=desc, path=str(path)))
    return out


def test_input_order_is_catalog_then_user_input_then_explicit_skill_body(tmp_path):
    skills = make_skills(tmp_path)

    items = build_selection_input("用 $commit-msg 写提交信息", skills)

    assert [i["role"] for i in items] == ["developer", "user", "user"]
    catalog = items[0]["content"]
    assert catalog.startswith("<skills_instructions>") and catalog.endswith("</skills_instructions>")
    assert f"- commit-msg: 生成提交信息 (file: {skills[0].path})" in catalog
    assert f"- code-review: 审查代码 (file: {skills[1].path})" in catalog
    assert items[1]["content"] == "用 $commit-msg 写提交信息"
    body = items[2]["content"]
    assert body.startswith("<skill>\n<name>commit-msg</name>\n<path>") and "COMMIT-MSG-BODY" in body
    assert "CODE-REVIEW-BODY" not in body  # 只有被点名的才有正文


def test_no_explicit_mention_means_no_body_item(tmp_path):
    items = build_selection_input("帮我看看代码", make_skills(tmp_path))

    assert [i["role"] for i in items] == ["developer", "user"]


def test_mention_of_unknown_skill_is_ignored(tmp_path):
    items = build_selection_input("用 $nope 试试", make_skills(tmp_path))

    assert len(items) == 2


def test_returns_name_and_path_of_the_skills_the_model_picked(tmp_path):
    skills = make_skills(tmp_path)
    provider = FakeProvider('{"skills": ["code-review"]}')

    refs = select_skills(provider, "审查一下 a.py", skills)

    assert refs == [SkillRef("code-review", skills[1].path)]
    call = provider.calls[0]
    assert call["tools"] == [] and call["system"]  # 内置上下文走 system，不给工具
    assert call["input"][1]["content"] == "审查一下 a.py"


def test_path_comes_from_the_catalog_not_from_the_model(tmp_path):
    skills = make_skills(tmp_path)
    provider = FakeProvider('{"skills": [{"name": "code-review", "path": "/etc/passwd"}]}')

    assert select_skills(provider, "x", skills) == [SkillRef("code-review", skills[1].path)]


def test_unknown_duplicate_and_garbage_are_dropped(tmp_path):
    skills = make_skills(tmp_path)

    assert select_skills(FakeProvider('{"skills": ["nope", "commit-msg", "commit-msg"]}'), "x", skills) == [
        SkillRef("commit-msg", skills[0].path)
    ]
    assert select_skills(FakeProvider("not json at all"), "x", skills) == []
    assert select_skills(FakeProvider('{"skills": "commit-msg"}'), "x", skills) == []


def test_accepts_json_wrapped_in_text_or_code_fence(tmp_path):
    skills = make_skills(tmp_path)
    reply = 'Sure:\n```json\n{"skills": ["commit-msg"]}\n```'

    assert select_skills(FakeProvider(reply), "x", skills) == [SkillRef("commit-msg", skills[0].path)]


def test_no_skills_means_no_request(tmp_path):
    provider = FakeProvider('{"skills": []}')

    assert select_skills(provider, "x", []) == []
    assert provider.calls == []
