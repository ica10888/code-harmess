"""Provider 层测试：注入 fake client，只验证「发出去的格式」和「回灌的格式」。"""

from types import SimpleNamespace as NS

import pytest

from providers import DeepSeekProvider, Reply, ToolCall, ToolResult, Usage
from tools import read_file


class FakeOpenAI:
    """记录 chat.completions.create 收到的参数，返回预设消息。"""

    def __init__(self, message, usage=None):
        self.calls: list[dict] = []
        self.chat = NS(completions=NS(create=self._create))
        self._message = message
        self._usage = usage

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return NS(choices=[NS(message=self._message)], usage=self._usage)


def _message(**overrides):
    base = {"content": None, "reasoning_content": None, "tool_calls": None}
    base.update(overrides)
    return NS(**base)


def test_declares_tools_in_openai_function_wrapper():
    client = FakeOpenAI(_message(content="hi"))
    DeepSeekProvider(client=client, model="m").chat([{"role": "user", "content": "x"}], [read_file])

    assert client.calls[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": read_file.description,
                "parameters": read_file.input_schema,
            },
        }
    ]


def test_omits_tools_param_when_no_tools():
    client = FakeOpenAI(_message(content="hi"))
    DeepSeekProvider(client=client, model="m").chat([{"role": "user", "content": "x"}], [])
    assert "tools" not in client.calls[0]


def test_system_prompt_is_sent_first_but_not_stored_in_conversation():
    client = FakeOpenAI(_message(content="hi"))
    conversation = [{"role": "user", "content": "x"}]

    DeepSeekProvider(client=client, model="m").chat(conversation, [], system="be brief")

    assert client.calls[0]["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "x"},
    ]
    assert conversation == [{"role": "user", "content": "x"}, {"role": "assistant", "content": "hi"}]


def test_no_system_message_when_system_prompt_empty():
    client = FakeOpenAI(_message(content="hi"))
    DeepSeekProvider(client=client, model="m").chat([{"role": "user", "content": "x"}], [])
    assert client.calls[0]["messages"][0]["role"] == "user"


def test_normalizes_reply_and_appends_assistant_with_reasoning():
    msg = _message(
        content="let me look",
        reasoning_content="thinking...",
        tool_calls=[NS(id="call_1", function=NS(name="read_file", arguments='{"path": "a.txt"}'))],
    )
    client = FakeOpenAI(msg)
    conversation = [{"role": "user", "content": "x"}]

    reply = DeepSeekProvider(client=client, model="m").chat(conversation, [read_file])

    assert reply == Reply(texts=["let me look"], tool_calls=[ToolCall("call_1", "read_file", {"path": "a.txt"})])
    assert conversation[-1] == {
        "role": "assistant",
        "content": "let me look",
        "reasoning_content": "thinking...",  # 思考模式 + tools 时必须原样回灌
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}
        ],
    }


@pytest.mark.parametrize("arguments", ['{"path": "a.txt"', "[1, 2]", "null"])
def test_malformed_arguments_become_input_error_instead_of_raising(arguments):
    msg = _message(tool_calls=[NS(id="call_1", function=NS(name="read_file", arguments=arguments))])

    reply = DeepSeekProvider(client=FakeOpenAI(msg), model="m").chat([], [read_file])

    [call] = reply.tool_calls
    assert call.id == "call_1"  # id 保留，才能配对回一条 role=tool 的错误结果
    assert call.input == {}
    assert call.input_error


def test_tool_results_become_role_tool_messages():
    provider = DeepSeekProvider(client=FakeOpenAI(_message()), model="m")
    out = provider.tool_results([ToolResult("c1", "file body"), ToolResult("c2", "no such file", is_error=True)])
    assert out == [
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
        {"role": "tool", "tool_call_id": "c2", "content": "Error: no such file"},
    ]


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider()


def test_model_defaults_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    assert DeepSeekProvider(client=FakeOpenAI(_message())).model == "deepseek-v4-pro"


def test_usage_is_normalized_including_deepseek_cache_hits():
    # 字段名取自真实 DeepSeek 响应：prompt_cache_hit_tokens 是它自动前缀缓存命中的部分
    usage = NS(prompt_tokens=938, completion_tokens=33, prompt_cache_hit_tokens=768)
    reply = DeepSeekProvider(client=FakeOpenAI(_message(content="hi"), usage), model="m").chat([], [])
    assert reply.usage == Usage(input_tokens=938, output_tokens=33, cached_tokens=768)


def test_missing_usage_is_none():
    reply = DeepSeekProvider(client=FakeOpenAI(_message(content="hi")), model="m").chat([], [])
    assert reply.usage is None


# --- prune_tool_results ------------------------------------------------------


def _conversation_with_tool_results(n, size=1000):
    conv = [{"role": "user", "content": "go"}]
    for i in range(n):
        conv.append(
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": f"think {i}",
                "tool_calls": [
                    {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
                ],
            }
        )
        conv.append({"role": "tool", "tool_call_id": f"c{i}", "content": str(i) * size})
    return conv


def _provider():
    return DeepSeekProvider(client=FakeOpenAI(_message()), model="m")


def test_prune_replaces_old_tool_results_with_placeholder_keeping_structure():
    conv = _conversation_with_tool_results(4)
    before = [(m["role"], m.get("tool_call_id")) for m in conv]

    assert _provider().prune_tool_results(conv, keep_last=2) == 2

    assert [(m["role"], m.get("tool_call_id")) for m in conv] == before  # 消息一条没少，id 仍然配对
    tool_msgs = [m for m in conv if m["role"] == "tool"]
    assert "pruned" in tool_msgs[0]["content"] and "read_file" in tool_msgs[0]["content"]
    assert "1,000 chars" in tool_msgs[0]["content"]
    assert tool_msgs[2]["content"] == "2" * 1000 and tool_msgs[3]["content"] == "3" * 1000


def test_prune_leaves_assistant_messages_and_reasoning_alone():
    conv = _conversation_with_tool_results(3)
    assistants = [dict(m) for m in conv if m["role"] == "assistant"]
    _provider().prune_tool_results(conv, keep_last=0)
    assert [m for m in conv if m["role"] == "assistant"] == assistants


def test_prune_is_idempotent_and_skips_short_results():
    conv = _conversation_with_tool_results(3, size=10)  # 比占位符还短，换了反而更长
    assert _provider().prune_tool_results(conv, keep_last=0) == 0

    conv = _conversation_with_tool_results(3)
    assert _provider().prune_tool_results(conv, keep_last=0) == 3
    assert _provider().prune_tool_results(conv, keep_last=0) == 0  # 已清理过的不再动


# --- 流式：on_text 逐段收到文字，拼出来的 assistant 消息和不流式时一样 -------------------


class FakeStreamingOpenAI:
    """stream=True 时 create 返回一串 chunk（最后一个只带 usage），记录收到的参数。"""

    def __init__(self, chunks):
        self.calls: list[dict] = []
        self.chat = NS(completions=NS(create=self._create))
        self._chunks = chunks

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._chunks)


def _chunk(content=None, reasoning=None, tool_calls=None, usage=None, choices=True):
    delta = NS(content=content, reasoning_content=reasoning, tool_calls=tool_calls)
    return NS(choices=[NS(delta=delta)] if choices else [], usage=usage)


def _tool_delta(index, id=None, name=None, arguments=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=arguments))


def test_streaming_calls_on_text_per_piece_and_builds_the_same_assistant_message():
    client = FakeStreamingOpenAI(
        [
            _chunk(reasoning="think "),
            _chunk(reasoning="more"),
            _chunk(content="Hel"),
            _chunk(content="lo"),
            _chunk(choices=False, usage=NS(prompt_tokens=7, completion_tokens=2, prompt_cache_hit_tokens=3)),
        ]
    )
    pieces: list[str] = []
    conversation: list[dict] = [{"role": "user", "content": "hi"}]

    reply = DeepSeekProvider(client=client).chat(conversation, [], "SYS", on_text=pieces.append)

    assert pieces == ["Hel", "lo"]  # 思考内容不外传
    assert reply.texts == ["Hello"]
    assert reply.usage == Usage(7, 2, 3)
    assert conversation[-1] == {"role": "assistant", "content": "Hello", "reasoning_content": "think more"}
    assert client.calls[0]["stream"] is True
    assert client.calls[0]["stream_options"] == {"include_usage": True}


def test_streaming_assembles_tool_calls_from_argument_fragments():
    client = FakeStreamingOpenAI(
        [
            _chunk(tool_calls=[_tool_delta(0, id="c1", name="read_file", arguments="")]),
            _chunk(tool_calls=[_tool_delta(0, arguments='{"path": ')]),
            _chunk(tool_calls=[_tool_delta(0, arguments='"a.txt"}')]),
            _chunk(tool_calls=[_tool_delta(1, id="c2", name="list_files", arguments="{}")]),
        ]
    )
    conversation: list[dict] = []

    reply = DeepSeekProvider(client=client).chat(conversation, [read_file], on_text=lambda t: None)

    assert reply.texts == []
    assert reply.tool_calls == [ToolCall("c1", "read_file", {"path": "a.txt"}), ToolCall("c2", "list_files", {})]
    assert [c["id"] for c in conversation[-1]["tool_calls"]] == ["c1", "c2"]  # 每个 id 都进了 assistant 消息


def test_without_on_text_the_request_is_not_streamed():
    client = FakeOpenAI(_message(content="hi"))

    DeepSeekProvider(client=client).chat([], [])

    assert "stream" not in client.calls[0] and "stream_options" not in client.calls[0]
