"""skills.py 的注入：把已经选好的 skill 文件全文，合成一条要放进对话的 user 消息。

和 agent.py 里 /skill名 调用时的做法一致（<skill name=...> 包着全文），区别是文件来自调用方给的路径列表
（后端的会话插件、默认插件），而不是按目录发现；格式不合格的跳过并说明，绝不让一个坏文件拖垮整次请求。
"""

import pytest

from skills import load_skill_file, skills_message

FM = "---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n"


def write(tmp_path, filename, name="demo", desc="做某件事", body="正文内容", raw=None):
    p = tmp_path / filename
    p.write_bytes(raw if raw is not None else FM.format(name=name, desc=desc, body=body).encode("utf-8"))
    return str(p)


def test_load_skill_file_reads_name_description_and_path(tmp_path):
    path = write(tmp_path, "any-name.md", name="stock-quote", desc="查询股价")

    skill = load_skill_file(path)

    assert (skill.name, skill.description) == ("stock-quote", "查询股价")
    assert skill.path.endswith("any-name.md")  # 文件名不用和 skill 名一致：这里是「一个 .md 一个 skill」，不是目录形式


def test_load_skill_file_accepts_chinese_file_names(tmp_path):
    assert load_skill_file(write(tmp_path, "代码审查.md", name="code-review")).name == "code-review"


@pytest.mark.parametrize(
    "raw, why",
    [
        (b"just text", "frontmatter"),
        (b"---\nname: x\n", "frontmatter"),
        (b"---\ndescription: d\n---\nbody", "name"),
        (b"---\nname: Bad Name\ndescription: d\n---\nbody", "name"),
        (b"---\nname: ok\n---\nbody", "description"),
    ],
)
def test_load_skill_file_rejects_files_without_valid_frontmatter(tmp_path, raw, why):
    with pytest.raises(ValueError, match=why):
        load_skill_file(write(tmp_path, "bad.md", raw=raw))


def test_skills_message_wraps_each_skill_full_text_in_a_skill_tag(tmp_path):
    a = write(tmp_path, "a.md", name="alpha", body="ALPHA-BODY")
    b = write(tmp_path, "b.md", name="beta", body="BETA-BODY")

    message, warnings = skills_message([a, b])

    assert warnings == []
    assert '<skill name="alpha"' in message and "ALPHA-BODY" in message
    assert '<skill name="beta"' in message and "BETA-BODY" in message
    assert message.index("ALPHA-BODY") < message.index("BETA-BODY")  # 按给的顺序
    assert message.count("</skill>") == 2


def test_skills_message_includes_the_whole_file_with_its_frontmatter_unchanged(tmp_path):
    path = write(tmp_path, "a.md", name="alpha", desc="描述", body="第一行\n第二行")

    message, _ = skills_message([path])

    assert FM.format(name="alpha", desc="描述", body="第一行\n第二行") in message


def test_skills_message_tells_the_model_how_to_treat_them(tmp_path):
    message, _ = skills_message([write(tmp_path, "a.md")])

    assert "skill" in message.lower() and "follow" in message.lower()
    # skill 是外部文本，不是更高权限的指令（和 prompt.py 里 # Skills 一节的立场一致）
    assert "can't override" in message or "cannot override" in message or "not a higher authority" in message


def test_skills_message_is_none_when_there_is_nothing_to_inject(tmp_path):
    assert skills_message([]) == (None, [])


def test_a_bad_file_is_skipped_with_a_warning_and_the_good_ones_still_go(tmp_path):
    good = write(tmp_path, "good.md", name="good", body="GOOD")
    bad = write(tmp_path, "bad.md", raw=b"no frontmatter")
    missing = str(tmp_path / "missing.md")

    message, warnings = skills_message([bad, good, missing])

    assert "GOOD" in message and "no frontmatter" not in message
    assert len(warnings) == 2
    assert "bad.md" in warnings[0] and "missing.md" in warnings[1]


def test_all_bad_means_no_message_but_the_warnings_are_kept(tmp_path):
    message, warnings = skills_message([write(tmp_path, "bad.md", raw=b"x")])
    assert message is None and len(warnings) == 1


def test_same_skill_name_twice_is_only_injected_once(tmp_path):
    one = write(tmp_path, "one.md", name="dup", body="FIRST")
    two = write(tmp_path, "two.md", name="dup", body="SECOND")

    message, warnings = skills_message([one, two])

    assert "FIRST" in message and "SECOND" not in message
    assert any("dup" in w for w in warnings)


def test_non_utf8_bytes_do_not_crash(tmp_path):
    raw = b"---\nname: ok\ndescription: d\n---\n\xff\xfe body"
    message, warnings = skills_message([write(tmp_path, "a.md", raw=raw)])
    assert message is not None and warnings == []


def test_total_size_is_capped_so_the_context_is_not_flooded(tmp_path):
    big = "x" * 40_000
    paths = [write(tmp_path, f"s{i}.md", name=f"s{i}", body=big) for i in range(4)]

    message, warnings = skills_message(paths)

    assert message.count("<skill ") < 4  # 放不下的不放
    assert any("太大" in w or "limit" in w.lower() or "skipped" in w.lower() for w in warnings)


def test_one_skill_that_alone_is_over_the_cap_is_skipped_with_a_warning(tmp_path):
    path = write(tmp_path, "huge.md", name="huge", body="y" * 200_000)
    message, warnings = skills_message([path])
    assert message is None and warnings
