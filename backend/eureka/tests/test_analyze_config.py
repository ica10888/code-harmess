"""示例插件 analyze_config：和默认工作流 analyze-config 同名。三个阶段：提取配置项 → 按类型整理 → 写入工作区。

payload 是触发 eureka 的那份 JSON（已通过 analyze-config 工作流的 schema）：
[{"type": "mysql" | "redis" | "kafka" | "zookeeper" | "elasticSearch", ...该类型的字段...}, ...]
"""

import json
from pathlib import Path

import pytest

from eureka import registry
from eureka.base import StageContext, StageResult, validate_stages
from eureka.plugins.analyze_config.plugin import PLUGIN, AnalyzeConfig

ITEMS = [
    {"type": "mysql", "address": "10.0.0.8", "port": "3306", "database": "shop", "username": "root", "password": "pw123"},
    {"type": "redis", "address": "10.0.0.5:6379,10.0.0.6:6379", "name": "cache", "password": "r-secret"},
    {"type": "kafka", "address": "k1:9092,k2:9092", "group": "g1", "topic": "t1,t2"},
    {"type": "mysql", "address": "10.0.0.9", "port": "3306", "database": "log"},
]


def ctx_for(tmp_path, payload=None, previous=None, run_id=3):
    lines = []
    ctx = StageContext(
        run_id=run_id, session_id="sess", payload=ITEMS if payload is None else payload,
        workspace=tmp_path, log=lines.append, previous=previous or {},
    )  # fmt: skip
    return ctx, lines


def extracted(tmp_path, payload=None):
    return {"extract": AnalyzeConfig().reconcile_extract(ctx_for(tmp_path, payload)[0]).output}


def classified(tmp_path, payload=None):
    prev = extracted(tmp_path, payload)
    prev["classify"] = AnalyzeConfig().reconcile_classify(ctx_for(tmp_path, payload, prev)[0]).output
    return prev


# --- 基本方法① ---


def test_the_plugin_is_named_like_the_workflow_it_belongs_to():
    assert AnalyzeConfig().name == "analyze-config"
    assert PLUGIN is AnalyzeConfig


def test_it_declares_three_stages_in_order_and_passes_the_main_projects_validation():
    stages = validate_stages(AnalyzeConfig())
    assert [s.key for s in stages] == ["extract", "classify", "write"]
    assert [s.method for s in stages] == ["reconcile_extract", "reconcile_classify", "reconcile_write"]
    assert all(s.title for s in stages)


def test_the_registry_can_load_it_next_to_the_other_plugin():
    reg = registry.load(["code_review", "analyze_config"])
    assert reg.warnings == []
    assert reg.names() == ["code-review", "analyze-config"]


# --- 阶段 1：提取配置项 ---


def test_extract_keeps_every_item_and_fills_the_missing_fields_with_empty_strings(tmp_path):
    out = AnalyzeConfig().reconcile_extract(ctx_for(tmp_path)[0]).output

    items = out["items"]
    assert len(items) == 4
    assert items[2] == {"type": "kafka", "address": "k1:9092,k2:9092", "password": "", "group": "g1", "topic": "t1,t2"}
    assert items[3]["password"] == "" and items[3]["username"] == ""  # 缺失的信息补成空串，下游不用到处判断 None


def test_extract_records_which_fields_were_missing_per_item(tmp_path):
    out = AnalyzeConfig().reconcile_extract(ctx_for(tmp_path)[0]).output
    assert out["missing"] == [{"index": 2, "type": "kafka", "fields": ["password"]}, {"index": 3, "type": "mysql", "fields": ["username", "password"]}]


def test_extract_does_not_report_a_field_that_the_type_does_not_have(tmp_path):
    # zookeeper 没有 username 这个字段：缺它不算缺失
    out = AnalyzeConfig().reconcile_extract(ctx_for(tmp_path, [{"type": "zookeeper", "address": "z1:2181"}])[0]).output
    assert out["missing"] == [{"index": 0, "type": "zookeeper", "fields": ["password"]}]
    assert "username" not in out["items"][0]


def test_extract_logs_how_many_items_and_how_many_have_gaps(tmp_path):
    ctx, lines = ctx_for(tmp_path)
    AnalyzeConfig().reconcile_extract(ctx)
    assert any("4" in line for line in lines) and any("缺" in line for line in lines)


def test_extract_handles_an_empty_list(tmp_path):
    ctx, lines = ctx_for(tmp_path, payload=[])
    out = AnalyzeConfig().reconcile_extract(ctx).output
    assert out == {"items": [], "missing": []} and any("0" in line for line in lines)


def test_extract_fails_loudly_when_the_payload_is_not_a_list(tmp_path):
    with pytest.raises(ValueError, match="列表"):
        AnalyzeConfig().reconcile_extract(ctx_for(tmp_path, payload={"type": "mysql"})[0])


def test_extract_rejects_an_unknown_type_instead_of_silently_dropping_it(tmp_path):
    with pytest.raises(ValueError, match="oracle"):
        AnalyzeConfig().reconcile_extract(ctx_for(tmp_path, payload=[{"type": "oracle"}])[0])


def test_extract_does_not_mutate_the_payload(tmp_path):
    payload = [{"type": "redis", "address": "r"}]
    AnalyzeConfig().reconcile_extract(ctx_for(tmp_path, payload=payload)[0])
    assert payload == [{"type": "redis", "address": "r"}]


def test_extract_is_idempotent(tmp_path):
    assert extracted(tmp_path) == extracted(tmp_path)


# --- 阶段 2：按类型整理 ---


def test_classify_groups_items_by_type_in_a_fixed_order_and_counts_them(tmp_path):
    out = AnalyzeConfig().reconcile_classify(ctx_for(tmp_path, previous=extracted(tmp_path))[0]).output

    assert out["counts"] == {"mysql": 2, "redis": 1, "kafka": 1}
    assert list(out["by_type"]) == ["mysql", "redis", "kafka"]  # 固定顺序：mysql、redis、kafka、zookeeper、elasticSearch
    assert [i["address"] for i in out["by_type"]["mysql"]] == ["10.0.0.8", "10.0.0.9"]  # 同类型保持原来的先后


def test_classify_leaves_out_types_that_have_no_items(tmp_path):
    out = AnalyzeConfig().reconcile_classify(ctx_for(tmp_path, previous=extracted(tmp_path))[0]).output
    assert "zookeeper" not in out["by_type"] and "elasticSearch" not in out["counts"]


def test_classify_counts_the_items_that_carry_a_plaintext_password(tmp_path):
    out = AnalyzeConfig().reconcile_classify(ctx_for(tmp_path, previous=extracted(tmp_path))[0]).output
    assert out["with_password"] == 2  # mysql 和 redis 各一个带密码


def test_classify_flags_a_cluster_address_with_several_nodes(tmp_path):
    out = AnalyzeConfig().reconcile_classify(ctx_for(tmp_path, previous=extracted(tmp_path))[0]).output
    assert out["clusters"] == [{"index": 1, "type": "redis", "nodes": 2}, {"index": 2, "type": "kafka", "nodes": 2}]


def test_classify_logs_a_per_type_summary(tmp_path):
    ctx, lines = ctx_for(tmp_path, previous=extracted(tmp_path))
    AnalyzeConfig().reconcile_classify(ctx)
    assert any("mysql" in line and "2" in line for line in lines)


def test_classify_on_an_empty_extract_is_fine(tmp_path):
    out = AnalyzeConfig().reconcile_classify(ctx_for(tmp_path, previous={"extract": {"items": [], "missing": []}})[0]).output
    assert out == {"counts": {}, "by_type": {}, "with_password": 0, "clusters": []}


def test_classify_without_the_previous_stage_output_fails_clearly(tmp_path):
    with pytest.raises(ValueError, match="extract"):
        AnalyzeConfig().reconcile_classify(ctx_for(tmp_path, previous={})[0])


# --- 阶段 3：写入工作区 ---


def write_ctx(tmp_path, payload=None, run_id=3):
    prev = classified(tmp_path, payload)
    return ctx_for(tmp_path, payload, prev, run_id=run_id)


def test_write_creates_a_json_and_a_markdown_file_in_the_workspace_and_returns_their_names(tmp_path):
    ctx, lines = write_ctx(tmp_path)

    out = AnalyzeConfig().reconcile_write(ctx).output

    assert out["files"] == ["analyze-config-3.json", "analyze-config-3.md"]
    for name in out["files"]:
        assert (tmp_path / name).is_file() and (tmp_path / name).parent == tmp_path


def test_write_tells_the_user_which_directory_the_files_went_to(tmp_path):
    # 工作流文件写的是「帮我写入到工作目录，并告诉我目录」
    ctx, lines = write_ctx(tmp_path)

    out = AnalyzeConfig().reconcile_write(ctx).output

    assert out["directory"] == str(tmp_path)
    assert any(str(tmp_path) in line for line in lines)
    assert any("analyze-config-3.json" in line for line in lines)


def test_the_json_file_holds_the_items_grouped_by_type(tmp_path):
    ctx, _ = write_ctx(tmp_path)
    AnalyzeConfig().reconcile_write(ctx)

    data = json.loads((tmp_path / "analyze-config-3.json").read_text(encoding="utf-8"))

    assert [i["type"] for i in data["items"]] == ["mysql", "redis", "kafka", "mysql"]
    assert data["counts"] == {"mysql": 2, "redis": 1, "kafka": 1}


def test_passwords_are_masked_in_both_files_nothing_in_plaintext_lands_in_the_workspace(tmp_path):
    ctx, _ = write_ctx(tmp_path)
    AnalyzeConfig().reconcile_write(ctx)

    for name in ("analyze-config-3.json", "analyze-config-3.md"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert "pw123" not in text and "r-secret" not in text, name  # 工作区里模型的文件工具能读到，所以不落明文密码

    data = json.loads((tmp_path / "analyze-config-3.json").read_text(encoding="utf-8"))
    assert data["items"][0]["password"] == "***" and data["items"][2]["password"] == ""  # 没有密码的仍然是空


def test_the_markdown_report_has_a_section_per_type_with_the_fields(tmp_path):
    ctx, _ = write_ctx(tmp_path)
    AnalyzeConfig().reconcile_write(ctx)

    text = (tmp_path / "analyze-config-3.md").read_text(encoding="utf-8")

    assert "# 配置文件分析报告" in text and "## mysql" in text and "## redis" in text and "## kafka" in text
    assert "10.0.0.8" in text and "shop" in text and "k1:9092,k2:9092" in text
    assert "## zookeeper" not in text  # 没有这个类型就不出这一节


def test_the_markdown_report_lists_the_missing_information(tmp_path):
    ctx, _ = write_ctx(tmp_path)
    AnalyzeConfig().reconcile_write(ctx)
    text = (tmp_path / "analyze-config-3.md").read_text(encoding="utf-8")
    assert "缺失" in text and "username" in text


def test_the_markdown_report_is_just_text_so_odd_values_do_no_harm(tmp_path):
    evil = [{"type": "mysql", "address": "<script>alert(1)</script>", "database": "```\n# injected heading"}]
    ctx, _ = write_ctx(tmp_path, payload=evil)
    AnalyzeConfig().reconcile_write(ctx)
    text = (tmp_path / "analyze-config-3.md").read_text(encoding="utf-8")
    assert "<script>" in text  # 原样文字
    assert "\n# injected heading" not in text  # 但不能借换行伪造出一个新标题


def test_rerunning_the_same_run_overwrites_the_same_files_instead_of_piling_up(tmp_path):
    ctx, _ = write_ctx(tmp_path)
    first = AnalyzeConfig().reconcile_write(ctx).output
    second = AnalyzeConfig().reconcile_write(ctx).output

    assert first == second and sorted(p.name for p in tmp_path.iterdir()) == ["analyze-config-3.json", "analyze-config-3.md"]


def test_two_different_runs_write_different_files(tmp_path):
    a = AnalyzeConfig().reconcile_write(write_ctx(tmp_path, run_id=3)[0]).output["files"]
    b = AnalyzeConfig().reconcile_write(write_ctx(tmp_path, run_id=4)[0]).output["files"]
    assert set(a).isdisjoint(b)


def test_write_never_goes_outside_the_workspace(tmp_path):
    ctx, _ = write_ctx(tmp_path)
    out = AnalyzeConfig().reconcile_write(ctx).output
    for name in out["files"]:
        assert Path(name).name == name and ".." not in name and "/" not in name and "\\" not in name


def test_write_creates_the_workspace_directory_if_it_does_not_exist_yet(tmp_path):
    ctx, _ = write_ctx(tmp_path)
    ctx.workspace = tmp_path / "input" / "sess" / "file"
    out = AnalyzeConfig().reconcile_write(ctx).output
    assert all((ctx.workspace / n).is_file() for n in out["files"])


def test_write_fails_clearly_without_a_workspace(tmp_path):
    ctx, _ = write_ctx(tmp_path)
    ctx.workspace = None
    with pytest.raises(ValueError, match="工作区"):
        AnalyzeConfig().reconcile_write(ctx)


def test_write_needs_the_classification_from_the_previous_stage(tmp_path):
    ctx, _ = ctx_for(tmp_path, previous=extracted(tmp_path))
    with pytest.raises(ValueError, match="classify"):
        AnalyzeConfig().reconcile_write(ctx)


def test_write_on_an_empty_result_still_writes_a_report_that_says_nothing_was_found(tmp_path):
    ctx, _ = write_ctx(tmp_path, payload=[])
    out = AnalyzeConfig().reconcile_write(ctx).output
    assert "没有" in (tmp_path / "analyze-config-3.md").read_text(encoding="utf-8") and len(out["files"]) == 2


# --- 整条链路 ---


def test_all_three_stages_chain_together_and_every_output_can_be_stored_in_the_database(tmp_path):
    plugin = AnalyzeConfig()
    previous: dict = {}
    for stage in validate_stages(plugin):
        ctx, _ = ctx_for(tmp_path, previous=dict(previous))
        previous[stage.key] = getattr(plugin, stage.method)(ctx).output

    assert set(previous) == {"extract", "classify", "write"}
    assert json.dumps(previous, ensure_ascii=False)


def test_the_stage_outputs_never_contain_a_plaintext_password_in_the_final_stage(tmp_path):
    # 前两个阶段的输出要存进数据库、进度接口不返回它们；但最后一个阶段的输出会被前端看到，必须干净
    plugin = AnalyzeConfig()
    previous: dict = {}
    for stage in validate_stages(plugin):
        ctx, _ = ctx_for(tmp_path, previous=dict(previous))
        previous[stage.key] = getattr(plugin, stage.method)(ctx).output
    assert "pw123" not in json.dumps(previous["write"]) and "r-secret" not in json.dumps(previous["write"])
