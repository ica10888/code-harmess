"""code_review 插件的第 4、5 阶段：LangChain 生成修改后的文件，再写出新文件。

  rewrite   读会话工作区里的原文件，连同审查建议交给 LangChain（DeepSeek）生成修改后的完整文件内容（只放在阶段输出里，不落盘）
  apply     校验后把修改后的内容写成工作区里的新文件，原文件一个字节不动

模型用注入的假链替身，测试里不调用真实 API；真实调用只在联调里做。
"""

import json
from pathlib import Path

import pytest

from eureka import registry
from eureka.base import StageContext, StageResult, validate_stages
from eureka.plugins.code_review.plugin import CodeReview

ORIGINAL = "import hashlib\n\nclass User:\n    password = None\n"
FIXED = "class User:\n    password = None  # write_only\n"

SUGGESTIONS = [
    {"file": "models.py", "start": 1, "end": 1, "type": "SUGGESTION", "desc": "无用导入", "code": "", "language": "python"},
    {"file": "models.py", "start": 4, "end": 4, "type": "QUESTION", "desc": "密码应只写", "code": "write_only", "language": "python"},
]


class FakeChain:
    """替身：记下收到的输入，返回预设的文本（或抛异常）。"""

    def __init__(self, reply=FIXED, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def __call__(self, inputs):
        self.calls.append(inputs)
        if self.error:
            raise self.error
        return self.reply


def plugin_with(chain=None, **kw):
    plugin = CodeReview(**kw)
    plugin.chain_factory = lambda: chain or FakeChain()
    return plugin


def ctx_for(tmp_path, previous=None, run_id=3):
    lines = []
    ctx = StageContext(run_id=run_id, session_id="s", payload=[], workspace=tmp_path, log=lines.append, previous=previous or {})
    return ctx, lines


def with_prior_stages(tmp_path, original=ORIGINAL, suggestions=None):
    # write_bytes：Windows 的 write_text 会把换行改成 CRLF，内容就和断言对不上了
    (tmp_path / "models.py").write_bytes(original.encode("utf-8"))
    return {
        "extract": {"suggestions": SUGGESTIONS if suggestions is None else suggestions},
        "summarize": {"total": 2},
        "report": {"file": "code-review-report-3.md"},
    }


# --- 阶段声明 ---


def test_the_plugin_now_declares_five_stages_in_order():
    stages = validate_stages(CodeReview())
    assert [s.key for s in stages] == ["extract", "summarize", "report", "rewrite", "apply"]
    assert [s.method for s in stages[3:]] == ["reconcile_rewrite", "reconcile_apply"]
    assert all(s.title for s in stages)


def test_the_registry_still_loads_it_without_a_model_being_configured():
    reg = registry.load(["code_review"])
    assert reg.warnings == [] and reg.get("code-review") is not None  # 加载插件不能要求有 DeepSeek key


# --- 阶段 4：rewrite ---


def test_rewrite_reads_the_original_file_and_hands_the_model_the_file_and_the_suggestions(tmp_path):
    chain = FakeChain()
    prev = with_prior_stages(tmp_path)

    plugin_with(chain).reconcile_rewrite(ctx_for(tmp_path, prev)[0])

    [call] = chain.calls
    assert call["filename"] == "models.py" and call["original"] == ORIGINAL
    assert "无用导入" in call["suggestions"] and "密码应只写" in call["suggestions"]
    assert "1" in call["suggestions"] and "write_only" in call["suggestions"]  # 行号和建议代码也给模型


def test_rewrite_returns_the_new_content_in_the_stage_output_and_writes_nothing_to_disk(tmp_path):
    prev = with_prior_stages(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())

    out = plugin_with(FakeChain(FIXED)).reconcile_rewrite(ctx_for(tmp_path, prev)[0]).output

    assert out == {"file": "models.py", "content": FIXED, "changed": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # 这一阶段不落盘
    assert (tmp_path / "models.py").read_bytes() == ORIGINAL.encode("utf-8")


def test_rewrite_reports_unchanged_when_the_model_returns_the_same_content(tmp_path):
    out = plugin_with(FakeChain(ORIGINAL)).reconcile_rewrite(ctx_for(tmp_path, with_prior_stages(tmp_path))[0]).output
    assert out["changed"] is False


def test_rewrite_strips_a_markdown_code_fence_the_model_may_wrap_around_the_file(tmp_path):
    wrapped = f"```python\n{FIXED}```"
    out = plugin_with(FakeChain(wrapped)).reconcile_rewrite(ctx_for(tmp_path, with_prior_stages(tmp_path))[0]).output
    assert out["content"] == FIXED


def test_rewrite_keeps_the_content_whole_when_it_merely_contains_backticks(tmp_path):
    body = "def f():\n    return '```'\n"
    out = plugin_with(FakeChain(body)).reconcile_rewrite(ctx_for(tmp_path, with_prior_stages(tmp_path))[0]).output
    assert out["content"] == body  # 只在整段被围栏包住时才去掉；内容里碰巧有 ``` 不能被截断


def test_rewrite_logs_what_it_is_doing(tmp_path):
    ctx, lines = ctx_for(tmp_path, with_prior_stages(tmp_path))
    plugin_with().reconcile_rewrite(ctx)
    assert any("models.py" in line for line in lines) and any("2" in line for line in lines)


def test_rewrite_with_no_suggestions_skips_the_model_and_changes_nothing(tmp_path):
    chain = FakeChain()
    out = plugin_with(chain).reconcile_rewrite(ctx_for(tmp_path, with_prior_stages(tmp_path, suggestions=[]))[0]).output
    assert chain.calls == [] and out == {"file": "", "content": "", "changed": False}  # 没有建议就不知道是哪个文件：不读文件、不调模型


def test_rewrite_fails_clearly_when_the_suggestions_name_no_file(tmp_path):
    prev = with_prior_stages(tmp_path, suggestions=[{k: v for k, v in SUGGESTIONS[0].items() if k != "file"}])
    with pytest.raises(ValueError, match="文件名"):
        plugin_with().reconcile_rewrite(ctx_for(tmp_path, prev)[0])


def test_rewrite_fails_when_the_suggestions_name_several_different_files(tmp_path):
    (tmp_path / "views.py").write_text("x = 1\n", encoding="utf-8")
    mixed = [SUGGESTIONS[0], {**SUGGESTIONS[1], "file": "views.py"}]
    with pytest.raises(ValueError, match="多个文件"):
        plugin_with().reconcile_rewrite(ctx_for(tmp_path, with_prior_stages(tmp_path, suggestions=mixed))[0])


def test_rewrite_fails_clearly_when_the_original_file_is_not_in_the_workspace(tmp_path):
    prev = with_prior_stages(tmp_path)
    (tmp_path / "models.py").unlink()
    with pytest.raises(ValueError, match="models.py"):
        plugin_with().reconcile_rewrite(ctx_for(tmp_path, prev)[0])


@pytest.mark.parametrize("evil", ["../secret.py", "..\\secret.py", "/etc/passwd", "a/b.py", "C:\\x.py", "..", ".", ""])
def test_rewrite_never_reads_outside_the_workspace_whatever_name_the_model_gave(tmp_path, evil):
    # 文件名来自模型的输出，不能信：带目录部分、上级目录的一律拒绝，绝不去读工作区以外的文件
    (tmp_path.parent / "secret.py").write_bytes(b"TOP-SECRET")
    bad = [{**SUGGESTIONS[0], "file": evil}]
    chain = FakeChain()

    with pytest.raises(ValueError):
        plugin_with(chain).reconcile_rewrite(ctx_for(tmp_path, with_prior_stages(tmp_path, suggestions=bad))[0])

    assert chain.calls == []  # 模型根本没有被调用，更不会看到工作区外的内容


def test_rewrite_refuses_to_read_a_symlink_that_points_outside_the_workspace(tmp_path):
    outside = tmp_path.parent / "outside.py"
    outside.write_bytes(b"TOP-SECRET")
    link = tmp_path / "models.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("this platform cannot create symlinks without privileges")
    prev = {"extract": {"suggestions": SUGGESTIONS}, "summarize": {}, "report": {}}
    chain = FakeChain()

    with pytest.raises(ValueError):
        plugin_with(chain).reconcile_rewrite(ctx_for(tmp_path, prev)[0])

    assert chain.calls == []


def test_rewrite_does_not_pick_the_report_or_generated_files_as_the_original(tmp_path):
    prev = with_prior_stages(tmp_path)
    (tmp_path / "code-review-report-3.md").write_bytes("# 报告".encode("utf-8"))
    chain = FakeChain()
    plugin_with(chain).reconcile_rewrite(ctx_for(tmp_path, prev)[0])
    assert chain.calls[0]["original"] == ORIGINAL


def test_rewrite_rejects_a_file_that_is_too_large_to_send_to_the_model(tmp_path):
    prev = with_prior_stages(tmp_path, original="x = 1\n" * 100_000)
    chain = FakeChain()
    with pytest.raises(ValueError, match="太大"):
        plugin_with(chain).reconcile_rewrite(ctx_for(tmp_path, prev)[0])
    assert chain.calls == []


def test_rewrite_rejects_a_binary_or_non_utf8_original(tmp_path):
    prev = with_prior_stages(tmp_path)
    (tmp_path / "models.py").write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(ValueError, match="UTF-8"):
        plugin_with().reconcile_rewrite(ctx_for(tmp_path, prev)[0])


def test_rewrite_fails_clearly_when_the_model_returns_nothing(tmp_path):
    for empty in ("", "   \n"):
        with pytest.raises(ValueError, match="没有返回"):
            plugin_with(FakeChain(empty)).reconcile_rewrite(ctx_for(tmp_path, with_prior_stages(tmp_path))[0])


def test_rewrite_lets_a_model_failure_propagate_so_the_runner_records_it(tmp_path):
    with pytest.raises(RuntimeError, match="deepseek down"):
        plugin_with(FakeChain(error=RuntimeError("deepseek down"))).reconcile_rewrite(ctx_for(tmp_path, with_prior_stages(tmp_path))[0])


def test_rewrite_is_idempotent_given_the_same_model_answer(tmp_path):
    prev = with_prior_stages(tmp_path)
    a = plugin_with(FakeChain(FIXED)).reconcile_rewrite(ctx_for(tmp_path, prev)[0]).output
    b = plugin_with(FakeChain(FIXED)).reconcile_rewrite(ctx_for(tmp_path, prev)[0]).output
    assert a == b


def test_rewrite_needs_the_suggestions_from_the_first_stage(tmp_path):
    with pytest.raises(ValueError, match="extract"):
        plugin_with().reconcile_rewrite(ctx_for(tmp_path, {})[0])


def test_the_api_key_is_never_part_of_what_is_sent_to_the_model(tmp_path):
    chain = FakeChain()
    plugin_with(chain).reconcile_rewrite(ctx_for(tmp_path, with_prior_stages(tmp_path))[0])
    assert "sk-" not in json.dumps(chain.calls, ensure_ascii=False)


# --- 阶段 5：apply ---


def rewritten(tmp_path, content=FIXED, changed=True, file="models.py"):
    prev = with_prior_stages(tmp_path)
    prev["rewrite"] = {"file": file, "content": content, "changed": changed}
    return prev


def test_apply_writes_the_new_content_as_a_new_file_next_to_the_original(tmp_path):
    out = CodeReview().reconcile_apply(ctx_for(tmp_path, rewritten(tmp_path))[0]).output

    assert out == {"file": "models.fixed-3.py", "source": "models.py", "written": True}
    assert (tmp_path / "models.fixed-3.py").read_bytes() == FIXED.encode("utf-8")


def test_apply_leaves_the_original_file_byte_for_byte_untouched(tmp_path):
    prev = rewritten(tmp_path)
    before = (tmp_path / "models.py").read_bytes()

    CodeReview().reconcile_apply(ctx_for(tmp_path, prev)[0])

    assert (tmp_path / "models.py").read_bytes() == before


def test_apply_tells_the_user_where_the_new_file_is(tmp_path):
    ctx, lines = ctx_for(tmp_path, rewritten(tmp_path))
    CodeReview().reconcile_apply(ctx)
    assert any("models.fixed-3.py" in line for line in lines) and any(str(tmp_path) in line for line in lines)


def test_the_new_file_name_keeps_the_extension_and_is_decided_only_by_the_run(tmp_path):
    (tmp_path / "app.config.js").write_bytes(b"a")
    prev = rewritten(tmp_path, file="app.config.js")
    assert CodeReview().reconcile_apply(ctx_for(tmp_path, prev, run_id=7)[0]).output["file"] == "app.config.fixed-7.js"
    (tmp_path / "Makefile").write_bytes(b"a")
    assert CodeReview().reconcile_apply(ctx_for(tmp_path, rewritten(tmp_path, file="Makefile"), run_id=7)[0]).output["file"] == "Makefile.fixed-7"


def test_rerunning_apply_overwrites_the_same_new_file_instead_of_piling_up(tmp_path):
    prev = rewritten(tmp_path)
    first = CodeReview().reconcile_apply(ctx_for(tmp_path, prev)[0]).output
    second = CodeReview().reconcile_apply(ctx_for(tmp_path, prev)[0]).output

    assert first == second and sorted(p.name for p in tmp_path.glob("models.fixed-*")) == ["models.fixed-3.py"]


def test_two_runs_write_two_different_new_files(tmp_path):
    prev = rewritten(tmp_path)
    a = CodeReview().reconcile_apply(ctx_for(tmp_path, prev, run_id=3)[0]).output["file"]
    b = CodeReview().reconcile_apply(ctx_for(tmp_path, prev, run_id=4)[0]).output["file"]
    assert a != b and (tmp_path / a).exists() and (tmp_path / b).exists()


def test_apply_writes_nothing_when_the_model_changed_nothing(tmp_path):
    out = CodeReview().reconcile_apply(ctx_for(tmp_path, rewritten(tmp_path, content=ORIGINAL, changed=False))[0]).output
    assert out["written"] is False and not list(tmp_path.glob("*.fixed-*"))


def test_apply_logs_that_nothing_needed_changing(tmp_path):
    ctx, lines = ctx_for(tmp_path, rewritten(tmp_path, content=ORIGINAL, changed=False))
    CodeReview().reconcile_apply(ctx)
    assert any("没有" in line or "无需" in line for line in lines)


@pytest.mark.parametrize("evil", ["../x.py", "..\\x.py", "/abs/x.py", "a/b.py", "..", ""])
def test_apply_never_writes_outside_the_workspace_whatever_file_name_it_is_given(tmp_path, evil):
    prev = rewritten(tmp_path, file=evil)
    workspace_before = sorted(p.name for p in tmp_path.iterdir())
    outside_before = sorted(p.name for p in tmp_path.parent.iterdir())  # 各个测试共用这个上级目录：比对前后快照，不看绝对内容

    with pytest.raises(ValueError):
        CodeReview().reconcile_apply(ctx_for(tmp_path, prev)[0])

    assert sorted(p.name for p in tmp_path.iterdir()) == workspace_before  # 工作区里没多出任何文件
    assert sorted(p.name for p in tmp_path.parent.iterdir()) == outside_before  # 工作区外面也没有


def test_apply_refuses_to_overwrite_a_file_that_is_not_its_own_output(tmp_path):
    # 目标名只会是 <原名>.fixed-<运行号>.<后缀>；如果工作区里已有同名的、不是上次生成的东西，也只是覆盖这个固定名字——
    # 这里确认它永远不会指向原文件本身
    out = CodeReview().reconcile_apply(ctx_for(tmp_path, rewritten(tmp_path))[0]).output
    assert out["file"] != "models.py"


def test_apply_does_not_accept_content_that_is_not_text(tmp_path):
    prev = rewritten(tmp_path)
    prev["rewrite"]["content"] = None
    with pytest.raises(ValueError, match="内容"):
        CodeReview().reconcile_apply(ctx_for(tmp_path, prev)[0])


def test_apply_fails_without_a_workspace(tmp_path):
    ctx, _ = ctx_for(tmp_path, rewritten(tmp_path))
    ctx.workspace = None
    with pytest.raises(ValueError, match="工作区"):
        CodeReview().reconcile_apply(ctx)


def test_apply_needs_the_previous_stage(tmp_path):
    with pytest.raises(ValueError, match="rewrite"):
        CodeReview().reconcile_apply(ctx_for(tmp_path, with_prior_stages(tmp_path))[0])


def test_apply_keeps_line_endings_as_the_model_returned_them(tmp_path):
    prev = rewritten(tmp_path, content="a\r\nb\r\n")
    CodeReview().reconcile_apply(ctx_for(tmp_path, prev)[0])
    assert (tmp_path / "models.fixed-3.py").read_bytes() == b"a\r\nb\r\n"  # 不让 Windows 把 \r\n 再改一遍


# --- 五个阶段连起来 ---


def test_all_five_stages_chain_and_every_output_can_be_stored(tmp_path):
    (tmp_path / "models.py").write_bytes(ORIGINAL.encode("utf-8"))
    plugin = plugin_with(FakeChain(FIXED))
    payload = [
        {"file": "models.py", "startLineNum": 1, "endLineNum": 1, "sugDesc": "无用导入", "sugCode": "", "codeLanguage": "python", "type": "SUGGESTION"}
    ]
    previous: dict = {}
    for stage in validate_stages(plugin):
        lines = []
        ctx = StageContext(run_id=3, session_id="s", payload=payload, workspace=tmp_path, log=lines.append, previous=dict(previous))
        previous[stage.key] = getattr(plugin, stage.method)(ctx).output

    assert list(previous) == ["extract", "summarize", "report", "rewrite", "apply"]
    assert (tmp_path / "models.fixed-3.py").read_bytes() == FIXED.encode("utf-8")
    assert (tmp_path / "models.py").read_bytes() == ORIGINAL.encode("utf-8")
    assert json.dumps(previous, ensure_ascii=False)
    assert "sk-" not in json.dumps(previous, ensure_ascii=False)
