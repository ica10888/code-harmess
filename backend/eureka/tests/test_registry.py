"""注册表测试：按 config.ini 的列表加载插件，按名称取。

对应参考仓库 init.go 里「选构造函数」的那一步：主项目只认接口，具体插件在这里选。
不动态加载用户上传的代码：只导入 backend/eureka/plugins/ 下、配置里点名的包。
"""

import sys
import textwrap

import pytest

from eureka import registry
from eureka.base import EurekaPlugin, Stage, StageResult

PLUGIN_SOURCE = """
from eureka.base import EurekaPlugin, Stage, StageResult


class {cls}(EurekaPlugin):
    name = "{name}"

    def stages(self):
        return [Stage("one", "一", "reconcile_one")]

    def reconcile_one(self, ctx):
        return StageResult()


PLUGIN = {cls}
"""


@pytest.fixture
def plugins_dir(tmp_path, monkeypatch):
    """造一个临时的 plugins 包，并让注册表从它里面导入（不污染真实的 backend/eureka/plugins）。"""
    package = tmp_path / "fake_plugins"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(registry, "PLUGINS_PACKAGE", "fake_plugins")
    yield package
    for name in [m for m in sys.modules if m == "fake_plugins" or m.startswith("fake_plugins.")]:
        del sys.modules[name]


def add_plugin(plugins_dir, folder, name, cls="P", source=None):
    d = plugins_dir / folder
    d.mkdir()
    (d / "__init__.py").write_text("", encoding="utf-8")
    (d / "plugin.py").write_text(textwrap.dedent(source or PLUGIN_SOURCE.format(cls=cls, name=name)), encoding="utf-8")


# --- 配置解析 ---


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", []),
        ("   ", []),
        ("code_review", ["code_review"]),
        ("a, b ,c", ["a", "b", "c"]),
        ("a,,b,", ["a", "b"]),
        ("a, a", ["a"]),  # 重复的只算一次
    ],
)
def test_the_plugin_list_is_parsed_from_the_config_value(raw, expected):
    assert registry.parse_plugin_list(raw) == expected


@pytest.mark.parametrize("bad", ["../evil", "a.b", "a/b", "A", "1a", "a-b", "名字", "a b"])
def test_folder_names_that_are_not_python_identifiers_are_rejected(bad):
    with pytest.raises(ValueError, match="插件目录名"):
        registry.parse_plugin_list(bad)


# --- 加载 ---


def test_a_listed_plugin_is_imported_and_found_by_its_workflow_name(plugins_dir):
    add_plugin(plugins_dir, "code_review", "code-review")

    reg = registry.load(["code_review"])

    plugin = reg.get("code-review")
    assert isinstance(plugin, EurekaPlugin) and plugin.name == "code-review"


def test_the_folder_name_and_the_plugin_name_may_differ(plugins_dir):
    # 目录名是 Python 包名（下划线），插件名是工作流 frontmatter 的 name（连字符）：注册表按后者找
    add_plugin(plugins_dir, "code_review", "code-review")
    reg = registry.load(["code_review"])
    assert reg.get("code_review") is None and reg.get("code-review") is not None


def test_names_lists_what_was_loaded_in_the_configured_order(plugins_dir):
    add_plugin(plugins_dir, "bbb", "bee")
    add_plugin(plugins_dir, "aaa", "ant")
    assert registry.load(["bbb", "aaa"]).names() == ["bee", "ant"]


def test_an_unknown_name_returns_none_instead_of_raising(plugins_dir):
    assert registry.load([]).get("nope") is None


def test_an_empty_list_loads_nothing(plugins_dir):
    reg = registry.load([])
    assert reg.names() == []


def test_the_same_plugin_instance_is_returned_every_time(plugins_dir):
    add_plugin(plugins_dir, "p", "pp")
    reg = registry.load(["p"])
    assert reg.get("pp") is reg.get("pp")


# --- 容错：一个坏插件不能让别的插件和整个服务起不来 ---


def test_a_folder_that_does_not_exist_is_skipped_with_a_warning_not_a_crash(plugins_dir):
    add_plugin(plugins_dir, "good", "good-one")
    reg = registry.load(["missing", "good"])
    assert reg.names() == ["good-one"]
    assert any("missing" in w for w in reg.warnings)


def test_a_plugin_that_raises_on_import_is_skipped(plugins_dir):
    add_plugin(plugins_dir, "broken", "x", source="raise RuntimeError('boom at import')")
    add_plugin(plugins_dir, "good", "good-one")

    reg = registry.load(["broken", "good"])

    assert reg.names() == ["good-one"]
    assert any("broken" in w and "boom at import" in w for w in reg.warnings)


def test_a_module_without_the_PLUGIN_marker_is_skipped(plugins_dir):
    add_plugin(plugins_dir, "nomark", "x", source="x = 1")
    reg = registry.load(["nomark"])
    assert reg.names() == [] and any("PLUGIN" in w for w in reg.warnings)


def test_a_PLUGIN_that_is_not_an_eureka_plugin_class_is_skipped(plugins_dir):
    add_plugin(plugins_dir, "wrong", "x", source="class Other: pass\nPLUGIN = Other")
    reg = registry.load(["wrong"])
    assert reg.names() == [] and any("EurekaPlugin" in w for w in reg.warnings)


def test_a_plugin_whose_stages_are_invalid_is_skipped_up_front(plugins_dir):
    source = PLUGIN_SOURCE.format(cls="P", name="bad-stages").replace('"reconcile_one"', '"reconcile_missing"')
    add_plugin(plugins_dir, "bad", "x", source=source)
    reg = registry.load(["bad"])
    assert reg.names() == [] and any("reconcile_missing" in w for w in reg.warnings)  # 加载时就发现，不等到运行


def test_a_plugin_whose_constructor_raises_is_skipped(plugins_dir):
    source = PLUGIN_SOURCE.format(cls="P", name="x").replace(
        "    def stages(self):", "    def __init__(self):\n        raise RuntimeError('ctor failed')\n\n    def stages(self):"
    )
    add_plugin(plugins_dir, "ctor", "x", source=source)
    reg = registry.load(["ctor"])
    assert reg.names() == [] and any("ctor failed" in w for w in reg.warnings)


def test_two_plugins_with_the_same_name_keep_the_first_and_warn(plugins_dir):
    add_plugin(plugins_dir, "one", "same")
    add_plugin(plugins_dir, "two", "same", cls="Q")
    reg = registry.load(["one", "two"])
    assert type(reg.get("same")).__name__ == "P"
    assert any("same" in w and "重名" in w for w in reg.warnings)


def test_loading_is_repeatable_and_does_not_leak_between_calls(plugins_dir):
    add_plugin(plugins_dir, "p", "pp")
    first = registry.load(["p"])
    second = registry.load([])
    assert first.names() == ["pp"] and second.names() == []


# --- 安全：不导入配置之外的东西 ---


def test_a_dotted_name_cannot_reach_other_packages(plugins_dir):
    # 带点的名字能让 import_module 导入任意模块（比如 os.path），所以解析阶段就拒绝
    with pytest.raises(ValueError):
        registry.parse_plugin_list("os.path")


def test_a_name_that_looks_like_a_traversal_is_rejected_before_anything_is_imported(plugins_dir):
    with pytest.raises(ValueError):
        registry.parse_plugin_list("../../etc")
