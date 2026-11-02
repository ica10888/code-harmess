"""示例插件 code_review：和默认工作流 code-review 同名。三个阶段：提取建议 → 汇总 → 写出报告文件。

payload 就是触发 eureka 的那份 JSON（已通过 code-review 工作流的 schema）：
[{"startLineNum", "endLineNum", "sugDesc", "sugCode", "codeLanguage", "type": "SUGGESTION" | "QUESTION"}, ...]
"""

import json
from pathlib import Path

import pytest

from eureka import registry
from eureka.base import StageContext, StageResult, validate_stages
from eureka.plugins.code_review.plugin import PLUGIN, CodeReview

SUGGESTIONS = [
    {"startLineNum": 22, "endLineNum": 22, "sugDesc": "密码未设为只写", "sugCode": "extra_kwargs = {}", "codeLanguage": "python", "type": "QUESTION"},
    {"startLineNum": 54, "endLineNum": 55, "sugDesc": "N+1 查询", "sugCode": "select_related()", "codeLanguage": "python", "type": "SUGGESTION"},
    {"startLineNum": 1, "endLineNum": 1, "sugDesc": "无用导入", "sugCode": "", "codeLanguage": "python", "type": "SUGGESTION"},
]


def ctx_for(tmp_path, payload=None, previous=None):
    lines = []
    ctx = StageContext(
        run_id=3, session_id="sess", payload=SUGGESTIONS if payload is None else payload,
        workspace=tmp_path, log=lines.append, previous=previous or {},
    )  # fmt: skip
    return ctx, lines


# --- 基本方法① ---


def test_the_plugin_is_named_like_the_workflow_it_belongs_to():
    assert CodeReview().name == "code-review"
    assert PLUGIN is CodeReview


def test_it_declares_three_stages_in_order_and_passes_the_main_projects_validation():
    stages = validate_stages(CodeReview())
    assert [s.key for s in stages] == ["extract", "summarize", "report"]
    assert [s.method for s in stages] == ["reconcile_extract", "reconcile_summarize", "reconcile_report"]
    assert all(s.title for s in stages)


def test_the_registry_can_load_it_by_its_folder_name():
    reg = registry.load(["code_review"])
    assert reg.warnings == [] and reg.get("code-review") is not None


# --- 阶段 1：提取建议 ---


def test_extract_normalizes_the_suggestions_and_reports_how_many(tmp_path):
    ctx, lines = ctx_for(tmp_path)

    result = CodeReview().reconcile_extract(ctx)

    assert isinstance(result, StageResult)
    items = result.output["suggestions"]
    assert [i["start"] for i in items] == [22, 54, 1] and items[1]["end"] == 55
    assert items[0] == {"start": 22, "end": 22, "type": "QUESTION", "desc": "密码未设为只写", "code": "extra_kwargs = {}", "language": "python"}
    assert any("3" in line for line in lines)  # 打印了提取到几条


def test_extract_handles_an_empty_list(tmp_path):
    ctx, lines = ctx_for(tmp_path, payload=[])
    assert CodeReview().reconcile_extract(ctx).output == {"suggestions": []}
    assert any("0" in line for line in lines)


def test_extract_fails_loudly_when_the_payload_is_not_a_list(tmp_path):
    ctx, _ = ctx_for(tmp_path, payload={"not": "a list"})
    with pytest.raises(ValueError, match="列表"):
        CodeReview().reconcile_extract(ctx)


def test_extract_is_idempotent(tmp_path):
    a = CodeReview().reconcile_extract(ctx_for(tmp_path)[0]).output
    b = CodeReview().reconcile_extract(ctx_for(tmp_path)[0]).output
    assert a == b


# --- 阶段 2：汇总 ---


def extracted(tmp_path):
    return {"extract": CodeReview().reconcile_extract(ctx_for(tmp_path)[0]).output}


def test_summarize_counts_by_type_and_lists_the_affected_lines(tmp_path):
    ctx, lines = ctx_for(tmp_path, previous=extracted(tmp_path))

    out = CodeReview().reconcile_summarize(ctx).output

    assert out["total"] == 3
    assert out["by_type"] == {"QUESTION": 1, "SUGGESTION": 2}
    assert out["lines"] == [1, 22, 54, 55]  # 去重、排序的受影响行号（范围展开）
    assert any("QUESTION" in line for line in lines)


def test_summarize_puts_the_serious_ones_first(tmp_path):
    ctx, _ = ctx_for(tmp_path, previous=extracted(tmp_path))
    out = CodeReview().reconcile_summarize(ctx).output
    assert out["ordered"][0]["type"] == "QUESTION"  # 风险类（QUESTION）排在建议类前面


def test_summarize_without_the_previous_stage_output_fails_clearly(tmp_path):
    ctx, _ = ctx_for(tmp_path, previous={})
    with pytest.raises(ValueError, match="extract"):
        CodeReview().reconcile_summarize(ctx)


def test_summarize_on_an_empty_result_is_fine(tmp_path):
    ctx, _ = ctx_for(tmp_path, previous={"extract": {"suggestions": []}})
    out = CodeReview().reconcile_summarize(ctx).output
    assert out["total"] == 0 and out["by_type"] == {} and out["lines"] == []


def test_summarize_expands_huge_ranges_safely(tmp_path):
    items = [{"start": 1, "end": 10_000_000, "type": "SUGGESTION", "desc": "d", "code": "", "language": "python"}]
    ctx, _ = ctx_for(tmp_path, previous={"extract": {"suggestions": items}})
    out = CodeReview().reconcile_summarize(ctx).output
    assert len(out["lines"]) <= 10_000  # 恶意的超大范围不能把内存吃光


# --- 阶段 3：写出报告文件 ---


def report_ctx(tmp_path):
    prev = extracted(tmp_path)
    prev["summarize"] = CodeReview().reconcile_summarize(ctx_for(tmp_path, previous=prev)[0]).output
    return ctx_for(tmp_path, previous=prev)


def test_report_writes_a_markdown_file_into_the_workspace_and_returns_its_name(tmp_path):
    ctx, lines = report_ctx(tmp_path)

    out = CodeReview().reconcile_report(ctx).output

    path = tmp_path / out["file"]
    assert path.is_file() and path.parent == tmp_path
    text = path.read_text(encoding="utf-8")
    assert "密码未设为只写" in text and "N+1 查询" in text and "22" in text
    assert any(out["file"] in line for line in lines)  # 告诉用户写到了哪


def test_report_file_name_is_fixed_per_run_so_a_rerun_overwrites_instead_of_piling_up(tmp_path):
    ctx, _ = report_ctx(tmp_path)
    first = CodeReview().reconcile_report(ctx).output["file"]
    second = CodeReview().reconcile_report(ctx).output["file"]

    assert first == second and len(list(tmp_path.iterdir())) == 1  # 幂等：重跑覆盖，不追加


def test_two_different_runs_write_different_files(tmp_path):
    ctx, _ = report_ctx(tmp_path)
    other = StageContext(run_id=4, session_id="sess", payload=[], workspace=tmp_path, log=lambda _: None, previous=ctx.previous)

    a = CodeReview().reconcile_report(ctx).output["file"]
    b = CodeReview().reconcile_report(other).output["file"]

    assert a != b


def test_report_never_writes_outside_the_workspace(tmp_path):
    ctx, _ = report_ctx(tmp_path)
    out = CodeReview().reconcile_report(ctx).output
    assert Path(out["file"]).name == out["file"]  # 只有文件名，没有目录部分
    assert ".." not in out["file"] and "/" not in out["file"] and "\\" not in out["file"]


def test_report_creates_the_workspace_directory_if_it_does_not_exist_yet(tmp_path):
    workspace = tmp_path / "input" / "sess" / "file"
    ctx, _ = report_ctx(tmp_path)
    ctx.workspace = workspace

    out = CodeReview().reconcile_report(ctx).output

    assert (workspace / out["file"]).is_file()


def test_report_fails_clearly_without_a_workspace(tmp_path):
    ctx, _ = report_ctx(tmp_path)
    ctx.workspace = None
    with pytest.raises(ValueError, match="工作区"):
        CodeReview().reconcile_report(ctx)


def test_report_needs_the_summary_from_the_previous_stage(tmp_path):
    ctx, _ = ctx_for(tmp_path, previous={"extract": extracted(tmp_path)["extract"]})
    with pytest.raises(ValueError, match="summarize"):
        CodeReview().reconcile_report(ctx)


def test_the_report_escapes_nothing_dangerous_it_is_just_text_in_a_file(tmp_path):
    evil = [{"start": 1, "end": 1, "type": "QUESTION", "desc": "<script>alert(1)</script>", "code": "```", "language": "python"}]
    ctx, _ = ctx_for(tmp_path, previous={"extract": {"suggestions": evil}, "summarize": {"total": 1, "by_type": {"QUESTION": 1}, "lines": [1], "ordered": evil}})
    out = CodeReview().reconcile_report(ctx).output
    assert (tmp_path / out["file"]).read_text(encoding="utf-8")  # 文件里是原样文字，不会被执行


# --- 整条链路 ---


def test_all_three_stages_chain_together_through_previous(tmp_path):
    plugin = CodeReview()
    previous: dict = {}
    for stage in validate_stages(plugin):
        ctx, _ = ctx_for(tmp_path, previous=dict(previous))
        previous[stage.key] = getattr(plugin, stage.method)(ctx).output
    assert set(previous) == {"extract", "summarize", "report"}
    assert json.dumps(previous)  # 每个阶段的输出都能存进数据库
