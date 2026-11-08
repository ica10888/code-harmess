"""Agent 循环测试：注入 fake provider 和脚本化输入，只看循环的形状。"""

import pytest

from agent import (
    MAX_TOOL_ROUNDS,
    Agent,
    ConsoleApprover,
    Session,
    _memory,
    _skills,
    command_argument,
    parse_args,
    persona_session,
    slash_command,
    tools_for_step,
)
from personas import Persona, load_persona
from prompt import PromptContext
from providers import Reply, ToolCall, ToolResult, Usage
from tools import ALL_TOOLS, Tool, read_file


class FakeProvider:
    label = "Fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.chats: list[list[dict]] = []  # 每次 chat 收到的 conversation 快照
        self.systems: list[str] = []
        self.tool_names: list[list[str]] = []  # 每次 chat 收到的工具名
        self.results: list[list[ToolResult]] = []
        self.prunes: list[int] = []  # 每次 prune_tool_results 收到的 keep_last

    def chat(self, conversation, tools, system=""):
        self.chats.append(list(conversation))
        self.systems.append(system)
        self.tool_names.append([t.name for t in tools])
        reply = self.replies.pop(0)
        conversation.append({"role": "assistant", "content": reply.texts})
        return reply

    def prune_tool_results(self, conversation, keep_last):
        self.prunes.append(keep_last)
        return 1

    def tool_results(self, results):
        self.results.append(results)
        return [{"role": "tool", "content": r.content} for r in results]


def scripted(*inputs):
    it = iter(inputs)
    return lambda: next(it, None)  # 输入用完 → None（相当于 Ctrl-D）


def test_tool_call_loops_back_to_model_without_asking_user(tmp_path, capsys):
    secret = tmp_path / "secret.txt"
    secret.write_text("neigh")
    provider = FakeProvider(
        [
            Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(secret)})]),
            Reply(texts=["a horse"]),
        ]
    )

    Agent(provider, [read_file], scripted("solve it")).run()

    assert len(provider.chats) == 2  # 只有一次用户输入，却问了模型两次
    assert provider.results == [[ToolResult("c1", "neigh")]]
    assert provider.chats[1][-1] == {"role": "tool", "content": "neigh"}
    assert "a horse" in capsys.readouterr().out


def test_reply_markdown_is_rendered_not_printed_raw(capsys):
    provider = FakeProvider([Reply(texts=["**bold** and `code`"])])
    Agent(provider, [], scripted("hi")).run()
    out = capsys.readouterr().out
    assert "bold" in out and "code" in out
    assert "**" not in out and "`" not in out


def test_token_usage_is_logged_to_stderr_not_stdout(capsys):
    provider = FakeProvider([Reply(texts=["hi"], usage=Usage(4210, 120, 3800))])
    Agent(provider, [], scripted("go")).run()
    out, err = capsys.readouterr()
    assert "in 4,210" in err and "cached 3,800" in err and "out 120" in err
    assert "4,210" not in out


def test_no_usage_line_when_provider_reports_none(capsys):
    Agent(FakeProvider([Reply(texts=["hi"])]), [], scripted("go")).run()
    assert capsys.readouterr().err == ""


def test_prunes_old_tool_results_when_input_exceeds_budget(capsys):
    provider = FakeProvider([Reply(texts=["hi"], usage=Usage(1001, 10))])
    Agent(provider, [], scripted("go"), context_budget=1000, keep_tool_results=5).run()
    assert provider.prunes == [5]
    assert "清理" in capsys.readouterr().err


def test_no_pruning_within_budget():
    provider = FakeProvider([Reply(texts=["hi"], usage=Usage(1000, 10))])
    Agent(provider, [], scripted("go"), context_budget=1000).run()
    assert provider.prunes == []


def test_tool_error_is_fed_back_not_raised(tmp_path):
    provider = FakeProvider(
        [
            Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(tmp_path / "missing")})]),
            Reply(texts=["sorry"]),
        ]
    )

    Agent(provider, [read_file], scripted("go")).run()

    [[result]] = provider.results
    assert result.is_error and result.call_id == "c1"


def test_unknown_tool_is_an_error_result():
    provider = FakeProvider([Reply(tool_calls=[ToolCall("c1", "rm_rf", {})]), Reply(texts=["ok"])])
    Agent(provider, [read_file], scripted("go")).run()
    [[result]] = provider.results
    assert result.is_error and "unknown tool" in result.content


def test_missing_argument_is_an_error_result():
    provider = FakeProvider([Reply(tool_calls=[ToolCall("c1", "read_file", {})]), Reply(texts=["ok"])])
    Agent(provider, [read_file], scripted("go")).run()
    [[result]] = provider.results
    assert result.is_error and "missing argument" in result.content


def test_input_error_is_an_error_result_and_tool_is_not_run(tmp_path):
    bad = ToolCall("c1", "read_file", {}, input_error="invalid JSON")
    provider = FakeProvider([Reply(tool_calls=[bad]), Reply(texts=["ok"])])
    Agent(provider, [read_file], scripted("go")).run()
    [[result]] = provider.results
    assert result.is_error and "invalid JSON" in result.content


def _dangerous_tool(ran: list):
    return Tool("danger", "d", {"type": "object"}, run=lambda args: ran.append(args) or "done", needs_approval=True)


@pytest.mark.parametrize("approved", [True, False])
def test_needs_approval_tool_runs_only_when_user_approves(approved):
    ran, asked = [], []
    provider = FakeProvider([Reply(tool_calls=[ToolCall("c1", "danger", {"x": 1})]), Reply(texts=["ok"])])

    Agent(provider, [_dangerous_tool(ran)], scripted("go"), approve=lambda call: asked.append(call) or approved).run()

    [[result]] = provider.results
    assert [c.id for c in asked] == ["c1"]
    assert ran == ([{"x": 1}] if approved else [])
    assert result.is_error is (not approved)


def test_tools_without_needs_approval_never_ask(tmp_path):
    provider = FakeProvider(
        [Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(tmp_path / "x")})]), Reply(texts=["ok"])]
    )
    Agent(provider, [read_file], scripted("go"), approve=lambda call: pytest.fail("should not ask")).run()


def _tool_reply(i):
    return Reply(tool_calls=[ToolCall(f"c{i}", "read_file", {"path": "/nonexistent"})])


def test_tool_rounds_cap_pauses_and_hands_back_to_user(capsys):
    provider = FakeProvider([_tool_reply(i) for i in range(10)])

    Agent(provider, [read_file], scripted("go"), max_tool_rounds=3).run()

    assert len(provider.chats) == 3  # 第 3 轮后不再问模型，回到用户（输入已用完 → 退出）
    assert len(provider.results) == 3  # 每轮的工具结果都回灌了，tool_call id 没有落单
    assert "3 轮" in capsys.readouterr().out


def test_tool_rounds_counter_resets_on_user_input():
    replies = [_tool_reply(1), _tool_reply(2), _tool_reply(3), Reply(texts=["done"])]
    provider = FakeProvider(replies)

    Agent(provider, [read_file], scripted("go", "继续"), max_tool_rounds=2).run()

    assert len(provider.chats) == 4  # 没重置的话，第 3 轮之后就又被暂停了
    assert provider.chats[2][-1] == {"role": "user", "content": "继续"}


def test_max_tool_rounds_must_be_positive():
    with pytest.raises(ValueError):
        Agent(FakeProvider([]), [], scripted(), max_tool_rounds=0)


def test_plain_text_reply_returns_to_user_and_keeps_history():
    provider = FakeProvider([Reply(texts=["hi"]), Reply(texts=["bye"])])

    Agent(provider, [], scripted("hello", "", "again")).run()  # 空行被跳过

    assert len(provider.chats) == 2
    assert provider.chats[1][0] == {"role": "user", "content": "hello"}  # 历史全量重发
    assert provider.chats[1][-1] == {"role": "user", "content": "again"}


def test_system_prompt_is_passed_on_every_model_call(tmp_path):
    provider = FakeProvider(
        [Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(tmp_path / "x")})]), Reply(texts=["ok"])]
    )
    Agent(provider, [read_file], scripted("go"), system="SYS").run()
    assert provider.systems == ["SYS", "SYS"]


def test_eof_exits_without_calling_model():
    provider = FakeProvider([])
    Agent(provider, [], scripted()).run()
    assert provider.chats == []


@pytest.mark.parametrize(
    "step,names",
    [
        (2, ["read_file"]),
        (3, ["read_file", "list_files"]),
        (4, ["read_file", "list_files", "edit_file"]),
        (5, ["read_file", "list_files", "edit_file", "run_bash"]),
    ],
)
def test_tools_for_step(step, names):
    assert [t.name for t in tools_for_step(step)] == names


def test_cli_defaults():
    args = parse_args([])
    assert (args.step, args.max_rounds) == (5, MAX_TOOL_ROUNDS)


def test_cli_max_rounds():
    assert parse_args(["--max-rounds", "5"]).max_rounds == 5


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_cli_rejects_non_positive_max_rounds(bad):
    with pytest.raises(SystemExit):
        parse_args(["--max-rounds", bad])


# --- ConsoleApprover：[Y/n/a] 审批 ---------------------------------------------


def _answers(*answers):
    """脚本化的键盘输入；记录被问了几次。用完抛 EOFError（相当于 Ctrl-D）。"""
    it = iter(answers)
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return read, prompts


def _call(name="run_bash"):
    return ToolCall("c1", name, {"command": "ls"})


@pytest.mark.parametrize("answer", ["", "y", "Y", "yes", "  y  "])
def test_approver_enter_or_y_allows_once(answer):
    read, _ = _answers(answer)
    assert ConsoleApprover(read)(_call()) is True


@pytest.mark.parametrize("answer", ["n", "N", "no"])
def test_approver_n_denies(answer):
    read, _ = _answers(answer)
    assert ConsoleApprover(read)(_call()) is False


def test_approver_ctrl_d_denies():
    read, _ = _answers()  # 没有输入 → EOFError
    assert ConsoleApprover(read)(_call()) is False


def test_approver_unrecognized_answer_asks_again():
    read, prompts = _answers("yse", "y")
    assert ConsoleApprover(read)(_call()) is True
    assert len(prompts) == 2


def test_approver_always_stops_asking_for_that_tool_this_session():
    read, prompts = _answers("a")
    approver = ConsoleApprover(read)
    assert approver(_call()) is True
    assert approver(_call()) is True
    assert approver(_call()) is True
    assert len(prompts) == 1  # 只问了第一次


def test_approver_always_is_per_tool():
    read, prompts = _answers("a", "n")
    approver = ConsoleApprover(read)
    assert approver(_call("run_bash")) is True
    assert approver(_call("deploy")) is False  # 别的工具照样要问
    assert len(prompts) == 2


def test_each_agent_gets_a_fresh_approver_session():
    # 默认审批器不能是模块级共享对象，否则上一个会话的「a」会带到下一个会话
    a1 = Agent(FakeProvider([]), [], scripted())
    a2 = Agent(FakeProvider([]), [], scripted())
    assert a1.approve is not a2.approve


# --- 记忆目录：默认 <cwd>/Memory ------------------------------------------------


def test_memory_defaults_to_memory_dir_in_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("MINI_HARNESS_MEMORY_DIR", raising=False)
    (tmp_path / "Memory").mkdir()
    (tmp_path / "Memory" / "n.md").write_text("# 项目笔记")
    memory_dir, index = _memory(str(tmp_path))
    assert memory_dir == str(tmp_path / "Memory")
    assert "项目笔记" in index


def test_missing_default_memory_dir_is_silent_but_still_writable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MINI_HARNESS_MEMORY_DIR", raising=False)
    # 还没记过东西：没有索引，但目录仍然给出——第一篇笔记由 edit_file 连目录一起建出来
    assert _memory(str(tmp_path)) == (str(tmp_path / "Memory"), None)
    assert capsys.readouterr().out == ""


def test_explicit_missing_memory_dir_warns_and_disables_memory(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINI_HARNESS_MEMORY_DIR", "nope")
    # 显式配置写错了：提醒，并且不让 agent 往一个拼错的新目录里写
    assert _memory(str(tmp_path)) == (None, None)
    assert "不存在" in capsys.readouterr().out


def test_explicit_relative_memory_dir_resolves_against_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_HARNESS_MEMORY_DIR", "notes")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "n.md").write_text("# 另一个目录")
    memory_dir, index = _memory(str(tmp_path))
    assert memory_dir == str(tmp_path / "notes") and "另一个目录" in index


# --- 斜杠命令 ------------------------------------------------------------------


@pytest.mark.parametrize("text", ["/exit", "/quit", "  /quit  ", "/EXIT", "/exit now"])
def test_exit_and_quit_end_the_session_without_calling_the_model(text):
    provider = FakeProvider([])
    Agent(provider, [], scripted(text, "never sent")).run()
    assert provider.chats == []


def test_exit_after_some_conversation_stops_there():
    provider = FakeProvider([Reply(texts=["hi"])])
    Agent(provider, [], scripted("hello", "/quit", "never sent")).run()
    assert len(provider.chats) == 1


def test_unknown_command_is_not_sent_to_model_and_lists_commands(capsys):
    provider = FakeProvider([Reply(texts=["ok"])])
    Agent(provider, [], scripted("/foo", "hello")).run()
    assert provider.chats[0] == [{"role": "user", "content": "hello"}]  # /foo 没进 conversation
    out = capsys.readouterr().out
    assert "/foo" in out and "/exit" in out and "/quit" in out


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/exit", "exit"),
        ("/Quit", "quit"),
        ("/help me", "help"),
        ("/Users/szou/a.py 看看这个", None),  # 路径不是命令：照常发给模型
        ("/", None),
        ("/123", None),
        ("hello /exit", None),
        ("", None),
    ],
)
def test_slash_command_parsing(text, expected):
    assert slash_command(text) == expected


def test_path_starting_with_slash_goes_to_the_model():
    provider = FakeProvider([Reply(texts=["ok"])])
    Agent(provider, [], scripted("/Users/szou/a.py 看看这个")).run()
    assert provider.chats[0][0]["content"] == "/Users/szou/a.py 看看这个"


# --- skill 目录：默认 <cwd>/skills，另加 MINI_HARNESS_SKILL_DIRS -------------------


def _write_skill(root, name, desc="d"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nbody\n")
    return d


def test_agent_specific_dirs_are_not_scanned_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MINI_HARNESS_SKILL_DIRS", raising=False)
    _write_skill(tmp_path / ".claude" / "skills", "claude-only")
    _write_skill(tmp_path / "skills", "old-location")
    assert _skills(str(tmp_path)) == []


# --- /skill名：用户主动调用 ------------------------------------------------------

from skills import discover_skills


def _skill_set(root, *specs):
    """specs: (name, frontmatter 额外行, 正文)。返回 discover_skills 的结果。"""
    for name, extra, body in specs:
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: desc of {name}\n{extra}---\n{body}\n")
    return discover_skills([str(root)])[0]


# --- persona：独立会话与切换 ------------------------------------------------------


def _session(name, tools=(), system=None, label=None, **kw):
    return Session(
        name=name,
        label=label or name,
        description=f"desc of {name}",
        system=system or f"SYS-{name}",
        tools={t.name: t for t in tools},
        **kw,
    )


def test_start_must_be_a_known_session():
    with pytest.raises(ValueError, match="nobody"):
        Agent(FakeProvider([]), [], scripted(), start="nobody")


def test_agent_properties_follow_current_session():
    agent = Agent(FakeProvider([]), [read_file], scripted(), system="SYS-D", sessions=[_session("b")])
    assert agent.system == "SYS-D" and list(agent.tools) == ["read_file"]
    agent.switch("b")
    assert agent.system == "SYS-b" and agent.tools == {}


# --- switch_summary：切换时打印的对比 ---------------------------------------------


# --- persona_session：persona → 会话 ----------------------------------------------


def _persona(pid="bajie", tools=("read_file", "list_files"), prompt="你是猪八戒。"):
    return Persona(id=pid, title="猪八戒", description="只看不改", tool_names=tools, prompt=prompt, emoji="🐷")


# --- 斜杠命令的参数 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [("/persona bajie", "bajie"), ("/persona   bajie  ", "bajie"), ("/persona", ""), ("/tools a b", "a b")],
)
def test_command_argument(text, expected):
    assert command_argument(text) == expected


# --- 查看命令：/prompt /tools /status ---------------------------------------------


def _run_and_capture(capsys, inputs, **kw):
    provider = FakeProvider(kw.pop("replies", []))
    agent = Agent(provider, kw.pop("tools", []), scripted(*inputs), **kw)
    agent.run()
    return agent, provider, capsys.readouterr().out


def test_prompt_command_prints_current_system_prompt_and_size(capsys):
    _, _, out = _run_and_capture(capsys, ["/prompt"], system="你是测试用的提示词")
    assert "你是测试用的提示词" in out
    assert "9 字符" in out


def test_tools_command_lists_chinese_summary_and_approval(capsys):
    _, _, out = _run_and_capture(capsys, ["/tools"], tools=ALL_TOOLS)
    assert "读取一个文件的内容" in out  # summary_zh
    bash_line = next(line for line in out.splitlines() if "run_bash" in line)
    assert "需审批" in bash_line
    read_line = next(line for line in out.splitlines() if line.strip().startswith("read_file"))
    assert "需审批" not in read_line


def test_tools_command_with_name_shows_english_description_and_schema(capsys):
    _, _, out = _run_and_capture(capsys, ["/tools read_file"], tools=[read_file])
    assert read_file.description in out
    assert '"required"' in out and '"path"' in out


def test_tools_command_with_unknown_name_is_reported(capsys):
    _, _, out = _run_and_capture(capsys, ["/tools nope"], tools=[read_file])
    assert "nope" in out and "read_file" in out


def test_tools_command_with_no_tools(capsys):
    _, _, out = _run_and_capture(capsys, ["/tools"])
    assert "没有工具" in out


def test_status_command_before_any_model_call(capsys, tmp_path):
    (tmp_path / "bajie").mkdir()
    (tmp_path / "bajie" / "n.md").write_text("# 笔记")
    sessions = [_session("bajie", ALL_TOOLS[:2], label="🐷 猪八戒", memory_dir=str(tmp_path / "bajie"))]
    _, _, out = _run_and_capture(capsys, ["/status"], sessions=sessions, start="bajie")
    assert "🐷 猪八戒" in out and "Fake" in out
    assert "2 个" in out and "read_file, list_files" in out
    assert "0 条" in out
    assert "—" in out  # 还没调用过模型
    assert str(tmp_path / "bajie") in out and "1 条笔记" in out


def test_status_command_shows_last_input_tokens(capsys):
    _, _, out = _run_and_capture(capsys, ["hi", "/status"], replies=[Reply(texts=["ok"], usage=Usage(4210, 10, 3800))])
    status = out.split("角色    ")[-1]  # /status 的第一行起
    assert "4,210" in status and "2 条" in status


def test_status_command_without_memory(capsys):
    _, _, out = _run_and_capture(capsys, ["/status"])
    assert "（无）" in out


@pytest.mark.parametrize("command", ["/prompt", "/tools", "/tools read_file", "/status", "/persona"])
def test_inspection_commands_never_reach_the_model(capsys, command):
    agent, provider, _ = _run_and_capture(capsys, [command], tools=[read_file])
    assert provider.chats == []
    assert agent.current.conversation == []


def test_inspection_commands_join_the_completion_menu():
    agent = Agent(FakeProvider([]), [], scripted())
    assert {"persona", "prompt", "tools", "status"} <= set(agent.commands)


# --- /persona reload：改完 persona 文件不用重启 -------------------------------------


def _reloadable(tmp_path, tools="read_file", body="你是猪八戒。"):
    """一个从文件读出来的 bajie 会话，加上 main() 里同样的 reload 函数。"""
    path = tmp_path / "bajie.md"

    def write(tools, body):
        path.write_text(f"---\nname: bajie\ndescription: d\ntools: {tools}\n---\n{body}\n", encoding="utf-8")

    write(tools, body)
    base = PromptContext(working_directory="/proj", tool_names=[])
    known = [t.name for t in ALL_TOOLS]
    session = persona_session(load_persona(str(path), known), base, None)

    def reload(s):
        return persona_session(load_persona(s.source, known), base, None)

    return session, reload, write


# --- 仓库里的三个西游记角色 ---------------------------------------------------------


# --- respond：没有终端的一问一答入口（后端用），和 run() 共用同一套一轮逻辑 ---------------


def test_respond_returns_the_model_text_and_keeps_the_conversation():
    provider = FakeProvider([Reply(texts=["one"]), Reply(texts=["two"])])
    agent = Agent(provider, [], scripted())

    first = agent.respond("a")
    second = agent.respond("b")

    assert first.texts == ["one"] and not first.paused
    assert second.texts == ["two"]
    assert [m["content"] for m in provider.chats[1] if m["role"] == "user"] == ["a", "b"]  # 第二轮带着第一轮


def test_respond_runs_tools_and_returns_texts_from_every_round(tmp_path):
    secret = tmp_path / "s.txt"
    secret.write_text("neigh")
    provider = FakeProvider(
        [
            Reply(texts=["let me look"], tool_calls=[ToolCall("c1", "read_file", {"path": str(secret)})]),
            Reply(texts=["a horse"]),
        ]
    )

    answer = Agent(provider, [read_file], scripted()).respond("go")

    assert answer.texts == ["let me look", "a horse"]
    assert provider.results == [[ToolResult("c1", "neigh")]]


def test_respond_denies_tools_that_need_approval_when_approver_says_no():
    bash = Tool("run_bash", "d", {}, lambda args: "ran", needs_approval=True)
    provider = FakeProvider([Reply(tool_calls=[ToolCall("c1", "run_bash", {})]), Reply(texts=["ok"])])

    Agent(provider, [bash], scripted(), approve=lambda call: False).respond("go")

    assert provider.results[0][0].is_error and "denied" in provider.results[0][0].content


def test_respond_pauses_at_the_round_limit_with_every_call_answered():
    calls = [Reply(tool_calls=[ToolCall(f"c{i}", "read_file", {"path": "."})]) for i in range(3)]
    provider = FakeProvider(calls)

    answer = Agent(provider, [read_file], scripted(), max_tool_rounds=3).respond("go")

    assert answer.paused
    assert sum(m["role"] == "tool" for m in provider.chats[-1]) == 2  # 前两轮的结果已回灌
    assert len(provider.results) == 3  # 第 3 轮的结果也回灌了（每个 tool_call 都有回复）


def test_respond_prunes_when_input_exceeds_the_budget():
    provider = FakeProvider([Reply(texts=["a"], usage=Usage(100, 1))])

    Agent(provider, [], scripted(), context_budget=10, keep_tool_results=4).respond("go")

    assert provider.prunes == [4]


def test_respond_failure_leaves_the_conversation_as_it_was():
    class Boom(FakeProvider):
        def chat(self, conversation, tools, system=""):
            conversation.append({"role": "assistant", "content": "half"})  # 失败前已经写了一半
            raise RuntimeError("boom")

    agent = Agent(Boom([]), [], scripted())
    agent.current.conversation.append({"role": "user", "content": "earlier"})

    with pytest.raises(RuntimeError):
        agent.respond("new")

    assert agent.current.conversation == [{"role": "user", "content": "earlier"}]


def test_run_still_works_after_sharing_the_round_logic_with_respond(capsys):
    provider = FakeProvider([Reply(texts=["hi there"], usage=Usage(100, 1))])

    Agent(provider, [], scripted("hello"), context_budget=10).run()

    assert "hi there" in capsys.readouterr().out
    assert provider.prunes  # 超预算修剪也在 run() 里照常发生


# --- respond 的流式：on_text 收到每一段文字 -------------------------------------------------


class StreamingProvider(FakeProvider):
    """和 FakeProvider 一样，但接受 on_text，并把每条回复的文字拆成两段推出去。"""

    def chat(self, conversation, tools, system="", on_text=None):
        reply = super().chat(conversation, tools, system)
        for text in reply.texts:
            half = len(text) // 2
            if on_text:
                on_text(text[:half])
                on_text(text[half:])
        return reply


def test_respond_streams_text_pieces_to_on_text():
    pieces: list[str] = []

    answer = Agent(StreamingProvider([Reply(texts=["abcd"])]), [], scripted()).respond("go", on_text=pieces.append)

    assert pieces == ["ab", "cd"]
    assert answer.texts == ["abcd"]


def test_respond_separates_the_text_of_different_rounds_in_the_stream(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("x")
    provider = StreamingProvider(
        [
            Reply(texts=["look"], tool_calls=[ToolCall("c1", "read_file", {"path": str(f)})]),
            Reply(texts=["done"]),
        ]
    )
    pieces: list[str] = []

    Agent(provider, [read_file], scripted()).respond("go", on_text=pieces.append)

    assert "".join(pieces) == "look\n\ndone"  # 和最后拼出来的回复一致，流式显示和最终消息不会对不上


def test_respond_without_on_text_still_works_with_providers_that_do_not_know_it():
    # FakeProvider.chat 没有 on_text 参数：没人要流式时，Agent 不能把这个参数传过去
    assert Agent(FakeProvider([Reply(texts=["ok"])]), [], scripted()).respond("go").texts == ["ok"]


def test_respond_stream_adds_one_separator_even_when_middle_rounds_say_nothing(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("x")
    tool = lambda i: Reply(tool_calls=[ToolCall(f"c{i}", "read_file", {"path": str(f)})])
    provider = StreamingProvider(
        [Reply(texts=["look"], tool_calls=tool(0).tool_calls), tool(1), tool(2), Reply(texts=["done"])]
    )
    pieces: list[str] = []

    answer = Agent(provider, [read_file], scripted()).respond("go", on_text=pieces.append)

    assert "".join(pieces) == "\n\n".join(answer.texts) == "look\n\ndone"  # 空轮次不能多出分隔符


def test_respond_stream_adds_no_separator_before_the_first_text(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("x")
    provider = StreamingProvider(
        [Reply(tool_calls=[ToolCall("c0", "read_file", {"path": str(f)})]), Reply(texts=["only"])]
    )
    pieces: list[str] = []

    Agent(provider, [read_file], scripted()).respond("go", on_text=pieces.append)

    assert "".join(pieces) == "only"  # 前面一轮没说话，开头不能有空行


# --- respond 的 model / effort：每次请求可以换，透传给 provider ----------------------------


class OptionsProvider(FakeProvider):
    """记录 chat 收到的 model / effort；和 StreamingProvider 一样，不认识这两个参数的 fake 不受影响。"""

    def __init__(self, replies):
        super().__init__(replies)
        self.options: list[dict] = []

    def chat(self, conversation, tools, system="", on_text=None, model=None, effort=None):
        self.options.append({"model": model, "effort": effort})
        return super().chat(conversation, tools, system)


def test_respond_passes_model_and_effort_to_the_provider_on_every_round(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("x")
    provider = OptionsProvider(
        [Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(f)})]), Reply(texts=["done"])]
    )

    Agent(provider, [read_file], scripted()).respond("go", model="deepseek-v4-pro", effort="max")

    assert provider.options == [{"model": "deepseek-v4-pro", "effort": "max"}] * 2  # 工具循环的每一轮都用同一组


def test_respond_without_model_and_effort_leaves_the_provider_defaults_alone():
    provider = OptionsProvider([Reply(texts=["ok"])])

    Agent(provider, [], scripted()).respond("go")

    assert provider.options == [{"model": None, "effort": None}]


def test_respond_does_not_pass_model_or_effort_to_providers_that_do_not_know_them():
    # FakeProvider.chat 没有 model / effort 参数：没人指定时，Agent 不能把它们传过去
    assert Agent(FakeProvider([Reply(texts=["ok"])]), [], scripted()).respond("go").texts == ["ok"]


def test_run_terminal_loop_is_unaffected_by_model_and_effort():
    provider = OptionsProvider([Reply(texts=["hi"])])
    Agent(provider, [], scripted("hello")).run()
    assert provider.options == [{"model": None, "effort": None}]


# --- respond 的 skills_message：勾选的 skill 全文放进这一轮的上下文 ---------------------------


def test_respond_puts_the_skills_message_in_the_conversation_before_the_users_text():
    provider = FakeProvider([Reply(texts=["ok"])])
    agent = Agent(provider, [], scripted())

    agent.respond("请审查", skills_message="<skill>X</skill>")

    sent = provider.chats[0]
    assert [m["role"] for m in sent] == ["user", "user"]
    assert sent[0]["content"] == "<skill>X</skill>" and sent[1]["content"] == "请审查"  # skill 在前，用户的话在后


def test_respond_without_skills_message_is_unchanged():
    provider = FakeProvider([Reply(texts=["ok"])])
    Agent(provider, [], scripted()).respond("hi")
    assert provider.chats[0] == [{"role": "user", "content": "hi"}]


def test_the_skills_message_stays_in_the_history_for_the_next_turn():
    provider = FakeProvider([Reply(texts=["one"]), Reply(texts=["two"])])
    agent = Agent(provider, [], scripted())

    agent.respond("first", skills_message="<skill>X</skill>")
    agent.respond("second")

    assert any(m["content"] == "<skill>X</skill>" for m in provider.chats[1])  # 第二轮也还带着（和 /skill名 一样）


def test_respond_failure_does_not_keep_the_skills_message_either():
    class Boom(FakeProvider):
        def chat(self, conversation, tools, system=""):
            raise RuntimeError("boom")

    agent = Agent(Boom([]), [], scripted())

    with pytest.raises(RuntimeError):
        agent.respond("hi", skills_message="<skill>X</skill>")

    assert agent.current.conversation == []


# --- 先选后载：正式请求之前，先问一次模型要加载哪些 skill，再把选中的正文放进对话 --------------


class SelectingProvider(FakeProvider):
    """第一次 chat 是「选 skill」的请求（没有工具），之后才是正式回复；接受 effort 等可选参数。"""

    def chat(self, conversation, tools, system="", on_text=None, model=None, effort=None):
        return super().chat(conversation, tools, system)


def _skill(tmp_path, name, body):
    from skills import load_skill_file

    path = tmp_path / f"{name}.md"
    path.write_text(f"---\nname: {name}\ndescription: 做 {name}\n---\n{body}\n", encoding="utf-8")
    return load_skill_file(str(path))


def test_respond_selects_then_loads_only_the_picked_skill_before_the_user_message(tmp_path):
    skills = [_skill(tmp_path, "alpha", "ALPHA-BODY"), _skill(tmp_path, "beta", "BETA-BODY")]
    provider = SelectingProvider([Reply(texts=['{"skills": ["beta"]}']), Reply(texts=["done"])])
    agent = Agent(provider, [], scripted(), skills=skills, select_skills=True)

    answer = agent.respond("请做 beta")

    assert answer.texts == ["done"]
    assert len(provider.chats) == 2
    assert provider.chats[0][1]["content"] == "请做 beta"  # 第一次是选择请求：目录 + 用户输入
    contents = [m["content"] for m in provider.chats[1]]
    assert "BETA-BODY" in contents[0] and "ALPHA-BODY" not in contents[0]  # 被选中的正文在用户消息之前
    assert contents[1] == "请做 beta"
    assert "BETA-BODY" in str(agent.current.conversation[0])  # 留在对话里


def test_nothing_selected_means_normal_conversation(tmp_path):
    provider = SelectingProvider([Reply(texts=['{"skills": []}']), Reply(texts=["done"])])
    agent = Agent(provider, [], scripted(), skills=[_skill(tmp_path, "alpha", "ALPHA-BODY")], select_skills=True)

    agent.respond("你好")

    assert [m["content"] for m in provider.chats[1]] == ["你好"]


def test_selection_failure_does_not_break_the_turn(tmp_path):
    class Broken(SelectingProvider):
        def chat(self, conversation, tools, system="", on_text=None, model=None, effort=None):
            if not self.chats:
                self.chats.append([])
                raise RuntimeError("boom")
            return super().chat(conversation, tools, system)

    provider = Broken([Reply(texts=["done"])])
    agent = Agent(provider, [], scripted(), skills=[_skill(tmp_path, "alpha", "A")], select_skills=True)

    assert agent.respond("hi").texts == ["done"]


def test_selection_can_be_turned_off(tmp_path):
    provider = SelectingProvider([Reply(texts=["done"])])

    Agent(provider, [], scripted(), skills=[_skill(tmp_path, "alpha", "A")], select_skills=False).respond("hi")

    assert len(provider.chats) == 1


def test_run_also_selects_before_each_user_message(tmp_path):
    provider = SelectingProvider([Reply(texts=['{"skills": ["alpha"]}']), Reply(texts=["ok"])])

    Agent(provider, [], scripted("hello"), skills=[_skill(tmp_path, "alpha", "ALPHA-BODY")], select_skills=True).run()

    assert "ALPHA-BODY" in provider.chats[1][0]["content"] and provider.chats[1][1]["content"] == "hello"


def test_selection_is_on_by_default(tmp_path):
    provider = SelectingProvider([Reply(texts=['{"skills": []}']), Reply(texts=["done"])])

    Agent(provider, [], scripted(), skills=[_skill(tmp_path, "alpha", "A")]).respond("hi")

    assert len(provider.chats) == 2  # 选择请求 + 正式请求


def test_no_select_skills_flag_turns_it_off():
    assert parse_args([]).no_select_skills is False
    assert parse_args(["--no-select-skills"]).no_select_skills is True


def test_a_skill_already_in_the_conversation_is_not_injected_again(tmp_path):
    skills = [_skill(tmp_path, "alpha", "ALPHA-BODY")]
    provider = SelectingProvider(
        [Reply(texts=['{"skills": ["alpha"]}']), Reply(texts=["one"]), Reply(texts=['{"skills": ["alpha"]}']), Reply(texts=["two"])]
    )
    agent = Agent(provider, [], scripted(), skills=skills)

    agent.respond("第一句")
    agent.respond("第二句")

    assert sum("ALPHA-BODY" in str(m["content"]) for m in agent.current.conversation if m.get("role") == "user") == 1


def test_respond_calls_extra_context_with_the_selected_names_and_appends_its_message_to_the_skill_message(tmp_path):
    skills = [_skill(tmp_path, "alpha", "ALPHA-BODY")]
    provider = SelectingProvider([Reply(texts=['{"skills": ["alpha"]}']), Reply(texts=["done"])])
    seen = []

    def extra(names):
        seen.append(names)
        return "EXTRA-FOR-" + ",".join(names)

    Agent(provider, [], scripted(), skills=skills).respond("hi", extra_context=extra)

    assert seen == [["alpha"]]
    contents = [m["content"] for m in provider.chats[1]]
    assert contents[0].index("ALPHA-BODY") < contents[0].index("EXTRA-FOR-alpha")  # 同一条消息里，skill 全文在前
    assert contents[1] == "hi"


def test_extra_context_is_asked_even_when_the_skill_is_already_in_the_conversation(tmp_path):
    skills = [_skill(tmp_path, "alpha", "ALPHA-BODY")]
    provider = SelectingProvider(
        [Reply(texts=['{"skills": ["alpha"]}']), Reply(texts=["one"]), Reply(texts=['{"skills": ["alpha"]}']), Reply(texts=["two"])]
    )
    seen = []
    agent = Agent(provider, [], scripted(), skills=skills)

    agent.respond("a", extra_context=lambda names: seen.append(names))
    agent.respond("b", extra_context=lambda names: seen.append(names))

    assert seen == [["alpha"], ["alpha"]]  # 回调自己决定要不要重复，core 只负责把选中的名字交出去


def test_extra_context_is_skipped_when_nothing_is_selected(tmp_path):
    provider = SelectingProvider([Reply(texts=['{"skills": []}']), Reply(texts=["done"])])
    called = []

    Agent(provider, [], scripted(), skills=[_skill(tmp_path, "alpha", "A")]).respond("hi", extra_context=called.append)

    assert called == []


def test_respond_reports_the_selected_skill_names_in_the_answer_even_if_already_loaded(tmp_path):
    skills = [_skill(tmp_path, "alpha", "ALPHA-BODY"), _skill(tmp_path, "beta", "BETA-BODY")]
    provider = SelectingProvider(
        [
            Reply(texts=['{"skills": ["alpha", "beta"]}']),
            Reply(texts=["one"]),
            Reply(texts=['{"skills": ["alpha"]}']),
            Reply(texts=["two"]),
        ]
    )
    agent = Agent(provider, [], scripted(), skills=skills)

    first = agent.respond("a")
    second = agent.respond("b")

    assert first.selected == ["alpha", "beta"]
    assert second.selected == ["alpha"]  # 这一轮选中了，哪怕全文已经在对话里、不再重复放


def test_answer_selected_is_empty_when_selection_is_off_or_nothing_is_picked(tmp_path):
    skills = [_skill(tmp_path, "alpha", "A")]

    off = Agent(SelectingProvider([Reply(texts=["x"])]), [], scripted(), skills=skills, select_skills=False).respond("a")
    none = Agent(SelectingProvider([Reply(texts=['{"skills": []}']), Reply(texts=["x"])]), [], scripted(), skills=skills).respond("a")

    assert off.selected == [] and none.selected == []


def test_respond_counts_requests_turns_and_tools(tmp_path):
    secret = tmp_path / "s.txt"
    secret.write_text("neigh")
    provider = FakeProvider(
        [
            Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(secret)})]),
            Reply(texts=["done"]),
        ]
    )
    agent = Agent(provider, [read_file], scripted(), select_skills=False)

    agent.respond("go")

    assert (agent.counter.requests, agent.counter.turns, agent.counter.tools) == (2, 1, 1)


def test_counter_records_request_and_tool_stats(tmp_path):
    secret = tmp_path / "s.txt"
    secret.write_text("neigh")
    provider = FakeProvider(
        [
            Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(secret)}), ToolCall("c2", "nope", {})]),
            Reply(texts=["done"], usage=Usage(input_tokens=10, output_tokens=3)),
        ]
    )
    agent = Agent(provider, [read_file], scripted(), select_skills=False)

    agent.respond("go")

    requests = agent.counter.request_stats
    assert [(r.input_tokens, r.output_tokens) for r in requests] == [(0, 0), (10, 3)]  # 没有 usage 记 0
    assert all(r.seconds >= 0 for r in requests)
    tools = agent.counter.tool_stats
    assert [(t.name, t.files_read, t.files_written) for t in tools] == [("read_file", 1, 0), ("nope", 0, 0)]  # 失败的不算文件


def test_counter_records_turn_stats(tmp_path):
    secret = tmp_path / "s.txt"
    secret.write_text("neigh")
    provider = FakeProvider(
        [
            Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(secret)})]),
            Reply(texts=["one"]),
            Reply(texts=["two"]),
        ]
    )
    agent = Agent(provider, [read_file], scripted(), select_skills=False)

    agent.respond("a")
    agent.respond("b")

    assert [(t.requests, t.tools) for t in agent.counter.turn_stats] == [(2, 1), (1, 0)]
    assert all(t.seconds >= 0 for t in agent.counter.turn_stats)


def test_request_stat_keeps_input_and_output_messages_and_tool_stat_keeps_paths(tmp_path):
    (tmp_path / "s.txt").write_text("neigh")
    provider = FakeProvider(
        [
            Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(tmp_path / "s.txt")})]),
            Reply(texts=["done"]),
        ]
    )
    agent = Agent(provider, [read_file], scripted(), select_skills=False)

    agent.respond("go")

    first, second = agent.counter.request_stats
    assert first.input_messages == [{"role": "user", "content": "go"}]  # 发出去时的样子，不含这次的回复
    assert first.output_messages == [{"role": "assistant", "content": []}]  # 这次新增的消息
    assert len(second.input_messages) == 3  # 用户的话、上一次的回复、工具结果
    assert agent.counter.tool_stats[0].paths == [str(tmp_path / "s.txt")]


def test_skill_selection_request_is_recorded_with_tokens_input_and_output(tmp_path):
    provider = SelectingProvider(
        [Reply(texts=['{"skills": ["alpha"]}'], usage=Usage(input_tokens=7, output_tokens=2)), Reply(texts=["done"])]
    )
    agent = Agent(provider, [], scripted(), skills=[_skill(tmp_path, "alpha", "A")], select_skills=True)

    agent.respond("请做 alpha")

    selection = agent.counter.request_stats[0]  # 第一条是选择请求
    assert (selection.input_tokens, selection.output_tokens) == (7, 2)
    assert selection.input_messages[1] == {"role": "user", "content": "请做 alpha"}  # 目录 + 用户输入
    assert selection.output_messages == [{"role": "assistant", "content": ['{"skills": ["alpha"]}']}]
    assert len(agent.counter.request_stats) == 2  # 选择请求 + 正式请求


def test_failed_skill_selection_request_is_not_recorded(tmp_path):
    class Broken(SelectingProvider):
        def chat(self, conversation, tools, system="", **kw):
            if not tools and len(self.chats) == 0:
                self.chats.append(list(conversation))
                raise RuntimeError("boom")
            return super().chat(conversation, tools, system, **kw)

    agent = Agent(Broken([Reply(texts=["done"])]), [], scripted(), skills=[_skill(tmp_path, "alpha", "A")], select_skills=True)

    agent.respond("hi")

    assert len(agent.counter.request_stats) == 1  # 只有正式请求
