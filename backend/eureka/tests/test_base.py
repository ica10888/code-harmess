"""插件接口测试：主项目只认 base.py 里的这几样东西，不认具体插件。

不依赖 Django：用一个最小的假插件和假上下文，验证接口约定本身。
"""

import pytest

from eureka.base import EurekaPlugin, Stage, StageContext, StageResult, validate_stages


class Demo(EurekaPlugin):
    name = "demo"

    def stages(self):
        return [Stage("extract", "提取", "reconcile_extract"), Stage("report", "报告", "reconcile_report")]

    def reconcile_extract(self, ctx):
        ctx.log("提取中")
        return StageResult(output={"n": 2})

    def reconcile_report(self, ctx):
        return StageResult()


def make_ctx(**kw):
    lines = []
    ctx = StageContext(run_id=1, session_id="s", payload={}, workspace=None, log=lines.append, **kw)
    return ctx, lines


# --- Stage 的约束 ---


def test_stage_is_immutable_and_has_the_three_fields():
    stage = Stage("extract", "提取", "reconcile_extract")
    assert (stage.key, stage.title, stage.method) == ("extract", "提取", "reconcile_extract")
    with pytest.raises(AttributeError):
        stage.key = "other"


@pytest.mark.parametrize("key", ["", "Extract", "a-b", "a b", "1abc", "名字", "a" * 41])
def test_stage_key_must_be_a_lowercase_identifier(key):
    with pytest.raises(ValueError, match="key"):
        Stage(key, "标题", "reconcile_x")


@pytest.mark.parametrize("key", ["a", "extract", "step_2", "a1_b2"])
def test_valid_stage_keys_are_accepted(key):
    Stage(key, "标题", "reconcile_x")


def test_stage_needs_a_title_and_a_method_name():
    with pytest.raises(ValueError, match="title"):
        Stage("a", "  ", "reconcile_a")
    with pytest.raises(ValueError, match="method"):
        Stage("a", "标题", "")
    with pytest.raises(ValueError, match="method"):
        Stage("a", "标题", "not a name")


# --- 基本方法①：stages() ---


def test_stages_is_the_first_required_method_a_plugin_without_it_cannot_be_created():
    class NoStages(EurekaPlugin):
        name = "x"

    with pytest.raises(TypeError):
        NoStages()


def test_stages_returns_the_plugins_stages_in_execution_order():
    assert [s.key for s in Demo().stages()] == ["extract", "report"]


def test_plugin_must_declare_a_name_that_is_a_valid_workflow_name():
    class Nameless(EurekaPlugin):
        def stages(self):
            return []

    with pytest.raises(TypeError, match="name"):
        Nameless()

    class Bad(Nameless):
        name = "Bad Name"

    with pytest.raises(ValueError, match="name"):
        Bad()


# --- validate_stages：主项目拿到 stages() 的返回值后先检查 ---


def test_validate_accepts_a_well_formed_plugin():
    assert [s.key for s in validate_stages(Demo())] == ["extract", "report"]


def test_validate_rejects_an_empty_stage_list():
    class Empty(Demo):
        def stages(self):
            return []

    with pytest.raises(ValueError, match="至少"):
        validate_stages(Empty())


def test_validate_rejects_duplicate_keys():
    class Dup(Demo):
        def stages(self):
            return [Stage("a", "一", "reconcile_extract"), Stage("a", "二", "reconcile_report")]

    with pytest.raises(ValueError, match="重复"):
        validate_stages(Dup())


def test_validate_rejects_a_stage_whose_method_does_not_exist_on_the_plugin():
    class Missing(Demo):
        def stages(self):
            return [Stage("a", "一", "reconcile_nope")]

    with pytest.raises(ValueError, match="reconcile_nope"):
        validate_stages(Missing())


def test_validate_rejects_a_method_name_that_is_not_callable():
    class NotCallable(Demo):
        reconcile_x = 5

        def stages(self):
            return [Stage("a", "一", "reconcile_x")]

    with pytest.raises(ValueError, match="reconcile_x"):
        validate_stages(NotCallable())


def test_validate_rejects_non_stage_items():
    class Junk(Demo):
        def stages(self):
            return ["extract"]

    with pytest.raises(ValueError, match="Stage"):
        validate_stages(Junk())


def test_validate_rejects_private_or_base_methods_as_stage_methods():
    # 阶段方法不能指向 stages 自己、或下划线开头的内部方法：避免一个坏插件让 runner 去调不该调的东西
    class Sneaky(Demo):
        def stages(self):
            return [Stage("a", "一", "stages")]

    with pytest.raises(ValueError, match="stages"):
        validate_stages(Sneaky())

    class Private(Demo):
        def _helper(self, ctx):
            return StageResult()

        def stages(self):
            return [Stage("a", "一", "_helper")]

    with pytest.raises(ValueError, match="_helper"):
        validate_stages(Private())


def test_validate_returns_a_copy_so_later_changes_to_the_plugins_list_do_not_matter():
    plugin = Demo()
    stages = validate_stages(plugin)
    assert isinstance(stages, tuple)


# --- 基本方法②：reconcile_<阶段>(ctx) -> StageResult ---


def test_a_stage_method_receives_the_context_and_returns_a_result():
    ctx, lines = make_ctx(previous={})

    result = Demo().reconcile_extract(ctx)

    assert result.output == {"n": 2}
    assert lines == ["提取中"]  # ctx.log 就是 webhook：打印的文字经它交给主项目


def test_stage_result_defaults_to_an_empty_output_and_instances_do_not_share_it():
    a, b = StageResult(), StageResult()
    a.output["x"] = 1
    assert b.output == {}


def test_context_exposes_what_a_stage_needs_and_nothing_about_django():
    ctx, _ = make_ctx(previous={"extract": {"n": 2}})
    assert (ctx.run_id, ctx.session_id, ctx.payload, ctx.previous) == (1, "s", {}, {"extract": {"n": 2}})
    assert not hasattr(ctx, "session") and not hasattr(ctx, "request")  # 插件碰不到 Django 对象


def test_context_previous_defaults_to_empty():
    ctx, _ = make_ctx()
    assert ctx.previous == {}


def test_context_log_rejects_non_text_and_truncates_long_lines():
    lines = []
    ctx = StageContext(run_id=1, session_id="s", payload={}, workspace=None, log=lines.append)

    ctx.log("a" * 5000)
    ctx.log(12345)  # 不是字符串：转成字符串，不报错（插件打印时不该因为类型崩）

    assert len(lines[0]) == 2000  # 单条上限，防止一行文字塞满数据库
    assert lines[1] == "12345"


def test_context_log_ignores_empty_lines():
    lines = []
    ctx = StageContext(run_id=1, session_id="s", payload={}, workspace=None, log=lines.append)
    ctx.log("")
    ctx.log("   ")
    assert lines == []


def test_context_has_a_cancel_flag_plugins_can_check_in_long_loops():
    ctx, _ = make_ctx()
    assert ctx.cancelled is False
    ctx.cancel()
    assert ctx.cancelled is True


def test_a_log_function_that_raises_never_breaks_the_stage():
    # webhook 发送失败只丢日志，不能让阶段失败（方案 §6）
    def broken(_):
        raise RuntimeError("webhook down")

    ctx = StageContext(run_id=1, session_id="s", payload={}, workspace=None, log=broken)

    ctx.log("hello")  # 不抛
