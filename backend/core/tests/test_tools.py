"""工具层测试：只通过 Tool.run() 这个公共接口验证行为。"""

import json
import os
import time

import pytest

import tools
from tools import ALL_TOOLS, ToolError, edit_file, list_files, read_file, run_bash


def test_all_tools_are_in_tutorial_order():
    assert [t.name for t in ALL_TOOLS] == ["read_file", "list_files", "edit_file", "run_bash"]


def test_every_tool_has_a_chinese_summary_for_display():
    for t in ALL_TOOLS:
        assert any("\u4e00" <= ch <= "\u9fff" for ch in t.summary_zh), t.name
        assert "\n" not in t.summary_zh, t.name  # 一行说明


# --- read_file ---------------------------------------------------------------


def test_read_file_returns_file_content(tmp_path):
    (tmp_path / "a.txt").write_text("hello 老周", encoding="utf-8")
    assert read_file.run({"path": str(tmp_path / "a.txt")}) == "hello 老周"


def _ten_lines(tmp_path):
    f = tmp_path / "ten.txt"
    f.write_text("".join(f"l{i}\n" for i in range(1, 11)))
    return str(f)


def test_read_file_long_file_shows_first_page_and_how_to_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "MAX_READ_LINES", 3)
    out = read_file.run({"path": _ten_lines(tmp_path)})
    assert out.startswith("l1\nl2\nl3\n")
    assert "l4" not in out
    assert "lines 1-3 of 10" in out and "offset=4" in out


def test_read_file_offset_and_limit_page_through(tmp_path):
    out = read_file.run({"path": _ten_lines(tmp_path), "offset": 4, "limit": 2})
    assert out.startswith("l4\nl5\n")
    assert "lines 4-5 of 10" in out and "offset=6" in out


def test_read_file_last_page_has_no_continue_hint(tmp_path):
    out = read_file.run({"path": _ten_lines(tmp_path), "offset": 9})
    assert out.startswith("l9\nl10\n")
    assert "lines 9-10 of 10" in out and "offset=" not in out


def test_read_file_limit_is_clamped_to_max(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "MAX_READ_LINES", 3)
    out = read_file.run({"path": _ten_lines(tmp_path), "limit": 100})
    assert "lines 1-3 of 10" in out


@pytest.mark.parametrize("args", [{"offset": 11}, {"offset": 0}, {"limit": 0}, {"offset": "2"}])
def test_read_file_rejects_bad_offset_or_limit(tmp_path, args):
    with pytest.raises(ToolError):
        read_file.run({"path": _ten_lines(tmp_path), **args})


def test_read_file_caps_characters_for_huge_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "MAX_READ_CHARS", 20)
    (tmp_path / "min.js").write_text("x" * 100)
    out = read_file.run({"path": str(tmp_path / "min.js")})
    assert out.startswith("x" * 20) and "truncated 80 chars" in out


def test_read_file_binary_raises_tool_error(tmp_path):
    (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00")
    with pytest.raises(ToolError, match="UTF-8"):
        read_file.run({"path": str(tmp_path / "img.png")})


def test_read_file_missing_raises_tool_error(tmp_path):
    with pytest.raises(ToolError):
        read_file.run({"path": str(tmp_path / "nope.txt")})


# --- list_files --------------------------------------------------------------


def test_list_files_returns_sorted_json_with_trailing_slash_dirs(tmp_path):
    (tmp_path / "b.py").write_text("")
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("")

    out = json.loads(list_files.run({"path": str(tmp_path)}))

    assert out == ["a.txt", "b.py", "sub/", "sub/c.md"]


def test_list_files_prunes_git_and_venv(tmp_path):
    for d in (".git", "__pycache__", ".venv", "venv"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "junk").write_text("")
    (tmp_path / "keep.py").write_text("")

    out = json.loads(list_files.run({"path": str(tmp_path)}))

    assert out == ["keep.py"]


def test_list_files_caps_entries_and_reports_total(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "MAX_LIST_ENTRIES", 3)
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("")
    out = list_files.run({"path": str(tmp_path)})
    shown, note = out.split("\n", 1)
    assert json.loads(shown) == ["f0.txt", "f1.txt", "f2.txt"]
    assert "3 of 5" in note


def test_list_files_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.txt").write_text("")
    assert json.loads(list_files.run({})) == ["x.txt"]


# --- edit_file ---------------------------------------------------------------


def test_edit_file_replaces_unique_match(tmp_path):
    f = tmp_path / "f.js"
    f.write_text("fizzBuzz(100);\n")

    assert edit_file.run({"path": str(f), "old_str": "100", "new_str": "15"}) == "OK"
    assert f.read_text() == "fizzBuzz(15);\n"


def test_edit_file_empty_old_str_creates_file_and_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "new.txt"

    msg = edit_file.run({"path": str(target), "old_str": "", "new_str": "content"})

    assert target.read_text() == "content"
    assert "created" in msg


def test_edit_file_empty_old_str_refuses_to_overwrite_existing_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("precious")
    with pytest.raises(ToolError, match="already exists"):
        edit_file.run({"path": str(f), "old_str": "", "new_str": "oops"})
    assert f.read_text() == "precious"


def test_edit_file_empty_old_str_fills_existing_empty_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("")
    edit_file.run({"path": str(f), "old_str": "", "new_str": "content"})
    assert f.read_text() == "content"


def test_edit_file_binary_raises_tool_error_and_leaves_file_alone(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\xff\xfe\x00abc")
    with pytest.raises(ToolError, match="UTF-8"):
        edit_file.run({"path": str(f), "old_str": "abc", "new_str": "xyz"})
    assert f.read_bytes() == b"\xff\xfe\x00abc"


def test_edit_file_rejects_missing_old_str(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("abc")
    with pytest.raises(ToolError, match="not found"):
        edit_file.run({"path": str(f), "old_str": "zzz", "new_str": "y"})


def test_edit_file_rejects_ambiguous_old_str(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("return None\nreturn None\n")
    with pytest.raises(ToolError, match="matched 2 times"):
        edit_file.run({"path": str(f), "old_str": "return None", "new_str": "return 1"})
    assert f.read_text() == "return None\nreturn None\n"  # 文件未被改坏


def test_edit_file_rejects_identical_old_and_new(tmp_path):
    with pytest.raises(ToolError, match="must differ"):
        edit_file.run({"path": str(tmp_path / "f"), "old_str": "a", "new_str": "a"})


# --- run_bash ----------------------------------------------------------------


def test_run_bash_needs_approval_and_others_do_not():
    assert [t.name for t in ALL_TOOLS if t.needs_approval] == ["run_bash"]


def test_run_bash_returns_stdout_and_stderr():
    out = run_bash.run({"command": "echo hello; echo oops >&2"})
    assert "hello" in out and "oops" in out


def test_run_bash_empty_output_is_explicit():
    assert run_bash.run({"command": "true"}) == "(no output)"


def test_run_bash_nonzero_exit_is_tool_error_with_output():
    with pytest.raises(ToolError, match="exit code 3") as e:
        run_bash.run({"command": "echo partial; exit 3"})
    assert "partial" in str(e.value)


def test_run_bash_timeout_kills_whole_process_group(monkeypatch):
    monkeypatch.setattr(tools, "BASH_TIMEOUT", 0.5)
    start = time.monotonic()
    with pytest.raises(ToolError, match="timed out"):
        run_bash.run({"command": "sleep 5; echo done"})  # sleep 是 sh 的孙进程，只杀 sh 会卡住等管道
    assert time.monotonic() - start < 3


def test_run_bash_keeps_tail_of_long_output(monkeypatch):
    # 测试失败、报错、堆栈都在输出末尾，所以截掉开头、保留结尾
    monkeypatch.setattr(tools, "MAX_OUTPUT_CHARS", 10)
    out = run_bash.run({"command": "printf 'HEAD%0100dTAIL' 0"})
    assert out.endswith("TAIL") and "HEAD" not in out
    assert "showing last 10 of 108 chars" in out


# --- workspace_tools：把读写限制在一个目录里（后端按会话各给一个）-------------------------------


def _ws(tmp_path):
    return {t.name: t for t in tools.workspace_tools(str(tmp_path / "ws"))}


def test_workspace_tools_keep_the_names_schemas_and_order_of_the_originals(tmp_path):
    ws = tools.workspace_tools(str(tmp_path))
    assert [t.name for t in ws] == ["read_file", "list_files", "edit_file", "run_bash"]
    for t in ws:
        original = next(o for o in ALL_TOOLS if o.name == t.name)
        assert t.input_schema == original.input_schema
        if t.name != "run_bash":  # run_bash 在网页里不审批，描述也相应改了
            assert (t.description, t.needs_approval) == (original.description, original.needs_approval)


def test_workspace_run_bash_needs_no_approval_and_runs_inside_the_workspace(tmp_path):
    bash = _ws(tmp_path)["run_bash"]
    assert not bash.needs_approval and "approve" not in bash.description

    bash.run({"command": "echo hi > made.txt"})

    assert (tmp_path / "ws" / "made.txt").exists()  # 命令的工作目录是工作区，不是服务进程所在目录


def test_workspace_directory_is_created_on_first_use(tmp_path):
    root = tmp_path / "input" / "sid" / "file"
    assert not root.exists()

    listing = {t.name: t for t in tools.workspace_tools(str(root))}["list_files"].run({})

    assert root.is_dir() and json.loads(listing) == []


def test_edit_file_creates_files_inside_the_workspace_and_read_and_list_see_them(tmp_path):
    ws = _ws(tmp_path)

    assert "Successfully created" in ws["edit_file"].run({"path": "notes/a.txt", "old_str": "", "new_str": "hello"})

    assert (tmp_path / "ws" / "notes" / "a.txt").read_text(encoding="utf-8") == "hello"
    assert "hello" in ws["read_file"].run({"path": "notes/a.txt"})
    assert json.loads(ws["list_files"].run({})) == ["notes/", os.path.join("notes", "a.txt")]
    assert not (tmp_path / "a.txt").exists()  # 没有写到别处


def test_edit_file_can_modify_an_existing_workspace_file(tmp_path):
    ws = _ws(tmp_path)
    ws["edit_file"].run({"path": "a.txt", "old_str": "", "new_str": "one two"})

    assert ws["edit_file"].run({"path": "a.txt", "old_str": "two", "new_str": "2"}) == "OK"

    assert (tmp_path / "ws" / "a.txt").read_text(encoding="utf-8") == "one 2"


def test_list_files_subpath_is_relative_to_the_workspace(tmp_path):
    ws = _ws(tmp_path)
    ws["edit_file"].run({"path": "d/x.txt", "old_str": "", "new_str": "x"})

    assert json.loads(ws["list_files"].run({"path": "d"})) == ["x.txt"]


@pytest.mark.parametrize("tool, args", [
    ("read_file", {"path": "../secret.txt"}),
    ("list_files", {"path": ".."}),
    ("edit_file", {"path": "../evil.txt", "old_str": "", "new_str": "x"}),
    ("edit_file", {"path": "a/../../evil.txt", "old_str": "", "new_str": "x"}),
])
def test_paths_that_climb_out_of_the_workspace_are_refused(tmp_path, tool, args):
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    ws = _ws(tmp_path)

    with pytest.raises(ToolError, match="outside the workspace"):
        ws[tool].run(args)

    assert not (tmp_path / "evil.txt").exists()


def test_absolute_paths_are_refused_even_when_they_point_inside_the_workspace(tmp_path):
    ws = _ws(tmp_path)
    ws["edit_file"].run({"path": "a.txt", "old_str": "", "new_str": "x"})
    inside = str(tmp_path / "ws" / "a.txt")

    for tool, args in [("read_file", {"path": inside}), ("list_files", {"path": inside})]:
        with pytest.raises(ToolError, match="relative"):
            ws[tool].run(args)


def test_a_symlink_pointing_out_of_the_workspace_cannot_be_followed(tmp_path):
    (tmp_path / "secret.txt").write_text("top secret", encoding="utf-8")
    ws = _ws(tmp_path)
    ws["list_files"].run({})  # 建出工作区
    try:
        (tmp_path / "ws" / "link.txt").symlink_to(tmp_path / "secret.txt")
    except OSError:
        pytest.skip("this platform cannot create symlinks without privileges")

    with pytest.raises(ToolError, match="outside the workspace"):
        ws["read_file"].run({"path": "link.txt"})


def test_workspaces_do_not_see_each_other(tmp_path):
    a = {t.name: t for t in tools.workspace_tools(str(tmp_path / "a"))}
    b = {t.name: t for t in tools.workspace_tools(str(tmp_path / "b"))}
    a["edit_file"].run({"path": "mine.txt", "old_str": "", "new_str": "x"})

    assert json.loads(b["list_files"].run({})) == []
    with pytest.raises(ToolError):
        b["read_file"].run({"path": "mine.txt"})


def test_errors_from_the_original_tools_still_come_back_as_tool_errors(tmp_path):
    ws = _ws(tmp_path)
    with pytest.raises(ToolError):
        ws["read_file"].run({"path": "missing.txt"})
    with pytest.raises(ToolError, match="must differ"):
        ws["edit_file"].run({"path": "a.txt", "old_str": "x", "new_str": "x"})


def test_the_original_tools_still_work_on_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    edit_file.run({"path": "plain.txt", "old_str": "", "new_str": "x"})

    assert (tmp_path / "plain.txt").exists()  # core 终端版的用法没变
