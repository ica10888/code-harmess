"""Provider 层测试（DeepSeek 原生 Responses API）：注入 fake client，只验证「发出去的格式」和「回灌的格式」。

对应 rule/deepseek-responses.md 第 3/4/6/8 节：input items、reasoning.effort、function_call_output、SSE 事件。
"""

import copy
from types import SimpleNamespace as NS

import pytest
from providers import DeepSeekProvider, ProviderError, ToolCall, ToolResult, Usage
from tools import read_file

# --- 假的 Responses 响应 / 流 --------------------------------------------------------------


def _reasoning(text="think"):
    return NS(type="reasoning", id="rs1", summary=[], content=[NS(type="reasoning_text", text=text)])


def _message(text="hi"):
    return NS(type="message", id="m1", role="assistant", content=[NS(type="output_text", text=text)])


def _call(call_id="call_1", name="read_file", arguments="{}"):
    return NS(type="function_call", id="f1", call_id=call_id, name=name, arguments=arguments)


def _usage(inp=10, out=5, cached=0):
    return NS(
        input_tokens=inp,
        output_tokens=out,
        input_tokens_details=NS(cached_tokens=cached),
        output_tokens_details=NS(reasoning_tokens=0),
    )


def _response(*output, status="completed", usage=None, incomplete_details=None, error=None):
    return NS(
        status=status,
        output=list(output),
        usage=usage,
        incomplete_details=incomplete_details,
        error=error,
        model="deepseek-flash",
    )


class FakeOpenAI:
    """记录 responses.create 收到的参数；返回预设的响应对象（或抛出预设的异常），流式时返回事件迭代器。"""

    def __init__(self, response=None, events=None, raises=None):
        self.calls: list[dict] = []
        self.responses = NS(create=self._create)
        self._response, self._events, self._raises = response, events, raises

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "input": copy.deepcopy(kwargs.get("input"))})  # 发送时的快照
        if self._raises:
            raise self._raises
        return iter(self._events) if kwargs.get("stream") else self._response


def _provider(response=None, **kwargs):
    client = kwargs.pop("client", None) or FakeOpenAI(response or _response(_message()))
    return DeepSeekProvider(client=client, model="deepseek-flash", **kwargs), client


# --- 请求格式 ------------------------------------------------------------------------------


def test_calls_responses_api_not_chat_completions():
    provider, client = _provider()
    provider.chat([{"role": "user", "content": "hi"}], [])
    assert len(client.calls) == 1
    assert not hasattr(client, "chat")  # 根本没有 chat.completions 可调


def test_declares_tools_flat_without_function_wrapper():
    provider, client = _provider()
    provider.chat([], [read_file])
    [tool] = client.calls[0]["tools"]
    assert tool == {
        "type": "function",
        "name": "read_file",
        "description": read_file.description,
        "parameters": read_file.input_schema,
    }  # 扁平，不是 {"function": {...}}


def test_omits_tools_param_when_no_tools():
    provider, client = _provider()
    provider.chat([], [])
    assert "tools" not in client.calls[0]


def test_system_prompt_goes_in_instructions_and_is_not_stored_in_conversation():
    provider, client = _provider()
    conversation = [{"role": "user", "content": "hi"}]
    provider.chat(conversation, [], "SYS")
    assert client.calls[0]["instructions"] == "SYS"
    assert client.calls[0]["input"] == [{"role": "user", "content": "hi"}]
    assert all(item.get("role") != "system" for item in conversation)


def test_no_instructions_when_system_prompt_empty():
    provider, client = _provider()
    provider.chat([{"role": "user", "content": "hi"}], [])
    assert "instructions" not in client.calls[0]


def test_uses_responses_field_names_and_none_of_the_chat_completions_ones():
    provider, client = _provider()
    provider.chat([{"role": "user", "content": "hi"}], [read_file], "S")
    sent = client.calls[0]
    assert sent["max_output_tokens"] > 0
    for banned in ("messages", "reasoning_effort", "max_tokens", "response_format", "stream_options", "temperature"):
        assert banned not in sent, banned
    for unsupported in ("previous_response_id", "conversation", "store", "background", "include", "metadata"):
        assert unsupported not in sent, unsupported  # 当前未支持，可能被静默忽略


def test_model_and_effort_are_sent_as_given():
    provider, client = _provider(reasoning_effort="max")
    provider.chat([], [])
    assert client.calls[0]["model"] == "deepseek-flash"
    assert client.calls[0]["reasoning"] == {"effort": "max"}


def test_model_and_effort_can_be_overridden_per_call_without_changing_the_provider():
    provider, client = _provider(reasoning_effort="low")
    provider.chat([], [], model="deepseek-v4-pro", effort="none")
    provider.chat([], [])
    assert (client.calls[0]["model"], client.calls[0]["reasoning"]) == ("deepseek-v4-pro", {"effort": "none"})
    assert (client.calls[1]["model"], client.calls[1]["reasoning"]) == ("deepseek-flash", {"effort": "low"})


@pytest.mark.parametrize("effort", ["none", "low", "high", "max"])
def test_all_four_efforts_are_accepted(effort):
    provider, client = _provider()
    provider.chat([], [], effort=effort)
    assert client.calls[0]["reasoning"] == {"effort": effort}


@pytest.mark.parametrize("kwargs", [{"effort": "medium"}, {"effort": ""}, {"model": "gpt-4"}, {"model": ""}])
def test_unknown_model_or_effort_is_rejected_before_any_request(kwargs):
    provider, client = _provider()
    with pytest.raises(ValueError):
        provider.chat([], [], **kwargs)
    assert client.calls == []  # 没花一分钱


def test_unknown_model_or_effort_in_the_constructor_is_rejected():
    with pytest.raises(ValueError):
        DeepSeekProvider(client=FakeOpenAI(), model="gpt-4")
    with pytest.raises(ValueError):
        DeepSeekProvider(client=FakeOpenAI(), model="deepseek-flash", reasoning_effort="medium")


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider()


def test_model_defaults_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    assert DeepSeekProvider(client=FakeOpenAI()).model == "deepseek-v4-pro"


def test_model_default_is_the_current_official_id():
    assert DeepSeekProvider(client=FakeOpenAI()).model == "deepseek-flash"


# --- 响应解析与历史 ------------------------------------------------------------------------


def test_text_reply_is_extracted_from_message_output_text_not_from_output_zero():
    # 答案不一定在 output[0]：前面常有 reasoning item
    provider, _ = _provider(_response(_reasoning(), _message("答案")))
    reply = provider.chat([], [])
    assert reply.texts == ["答案"] and reply.tool_calls == []


def test_assistant_output_is_appended_as_supported_input_items_in_order():
    provider, _ = _provider(
        _response(_reasoning("想想"), _message("先说一句"), _call("c1", "read_file", '{"path":"a"}'))
    )
    conversation: list[dict] = []
    provider.chat(conversation, [])
    assert conversation == [
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "想想"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "先说一句"}]},
        {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": '{"path":"a"}'},
    ]


def test_reasoning_summary_and_encrypted_content_are_not_sent_back():
    item = NS(
        type="reasoning", id="r", summary=["s"], encrypted_content="xx", content=[NS(type="reasoning_text", text="t")]
    )
    provider, _ = _provider(_response(item, _message()))
    conversation: list[dict] = []
    provider.chat(conversation, [])
    assert conversation[0] == {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "t"}]}


def test_missing_reasoning_is_not_fabricated():
    provider, _ = _provider(_response(_message("hi")))
    conversation: list[dict] = []
    provider.chat(conversation, [])
    assert [i["type"] for i in conversation] == ["message"]


def test_unknown_output_item_type_is_an_explicit_error():
    provider, _ = _provider(_response(NS(type="web_search_call", id="w"), _message()))
    with pytest.raises(ProviderError, match="web_search_call"):
        provider.chat([], [])


def test_function_call_arguments_are_parsed_into_a_dict():
    provider, _ = _provider(_response(_call("c1", "read_file", '{"path": "a.txt"}')))
    assert provider.chat([], []).tool_calls == [ToolCall("c1", "read_file", {"path": "a.txt"})]


def test_same_round_multiple_calls_are_all_returned_in_order():
    provider, _ = _provider(_response(_call("c1"), _call("c2", "list_files")))
    assert [c.id for c in provider.chat([], []).tool_calls] == ["c1", "c2"]


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]", '"x"', "null"])
def test_malformed_arguments_become_input_error_instead_of_raising(arguments):
    # 调用已 append 进 conversation：必须还能配一条 function_call_output 错误结果，不能抛
    provider, _ = _provider(_response(_call("c9", "read_file", arguments)))
    conversation: list[dict] = []
    [call] = provider.chat(conversation, []).tool_calls
    assert call.id == "c9" and call.input == {} and call.input_error
    assert conversation[-1]["call_id"] == "c9"


def test_tool_results_become_function_call_output_items():
    provider, _ = _provider()
    assert provider.tool_results([ToolResult("c1", "file body"), ToolResult("c2", "no such file", is_error=True)]) == [
        {"type": "function_call_output", "call_id": "c1", "output": "file body"},
        {"type": "function_call_output", "call_id": "c2", "output": "Error: no such file"},
    ]


def test_second_round_really_carries_the_first_round_history():
    provider, client = _provider(_response(_message("one")))
    conversation = [{"role": "user", "content": "first"}]
    provider.chat(conversation, [])
    conversation.append({"role": "user", "content": "second"})
    client.calls.clear()
    provider.chat(conversation, [])
    sent = client.calls[0]["input"]
    assert [i.get("role") for i in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"] == "first" and sent[2]["content"] == "second"


def test_usage_is_normalized_including_cache_hits_and_missing_is_none_not_zero():
    provider, _ = _provider(_response(_message(), usage=_usage(938, 33, 768)))
    assert provider.chat([], []).usage == Usage(input_tokens=938, output_tokens=33, cached_tokens=768)
    provider, _ = _provider(_response(_message(), usage=_usage(5, 0, 0)))
    assert provider.chat([], []).usage == Usage(5, 0, 0)  # 0 就是 0
    provider, _ = _provider(_response(_message(), usage=None))
    assert provider.chat([], []).usage is None  # 缺失就是缺失


# --- 终态：completed / incomplete / failed -------------------------------------------------


def test_incomplete_keeps_partial_text_but_is_not_reported_as_completed_and_runs_no_tools():
    provider, _ = _provider(
        _response(_message("半截"), _call("c1"), status="incomplete", incomplete_details=NS(reason="max_output_tokens"))
    )
    with pytest.raises(ProviderError) as e:
        provider.chat([], [])
    assert e.value.status == "incomplete" and "max_output_tokens" in str(e.value)
    assert e.value.partial_texts == ["半截"]  # 已有的文字保留给调用方


def test_failed_keeps_the_error():
    provider, _ = _provider(_response(status="failed", error=NS(code="server_error", message="boom")))
    with pytest.raises(ProviderError, match="boom") as e:
        provider.chat([], [])
    assert e.value.status == "failed"


def test_incomplete_or_failed_response_does_not_append_anything_to_the_conversation():
    provider, _ = _provider(_response(_message("x"), status="incomplete", incomplete_details=NS(reason="r")))
    conversation = [{"role": "user", "content": "hi"}]
    with pytest.raises(ProviderError):
        provider.chat(conversation, [])
    assert conversation == [{"role": "user", "content": "hi"}]


# --- 错误与重试 ----------------------------------------------------------------------------


class _HTTPError(Exception):
    def __init__(self, status):
        super().__init__(f"http {status}")
        self.status_code = status


def _retrying(raises_then, **kwargs):
    """前几次抛错、最后成功的 client；sleep 换成记录器，测试不真等。"""
    seq = list(raises_then)

    class Client(FakeOpenAI):
        def _create(self, **kw):
            self.calls.append({**kw, "input": copy.deepcopy(kw.get("input"))})
            item = seq.pop(0) if seq else _response(_message("ok"))
            if isinstance(item, Exception):
                raise item
            return item

    client = Client()
    sleeps: list[float] = []
    return DeepSeekProvider(client=client, model="deepseek-flash", sleep=sleeps.append, **kwargs), client, sleeps


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_errors_are_retried_with_exponential_backoff(status):
    provider, client, sleeps = _retrying([_HTTPError(status), _HTTPError(status)])
    assert provider.chat([], []).texts == ["ok"]
    assert len(client.calls) == 3
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] > 0


def test_retry_gives_up_after_the_limit_and_reports_clearly():
    provider, client, _ = _retrying([_HTTPError(503)] * 10)
    with pytest.raises(ProviderError, match="503"):
        provider.chat([], [])
    assert 1 < len(client.calls) <= 5  # 有上限


@pytest.mark.parametrize("status", [400, 401, 402])
def test_client_errors_are_not_retried(status):
    provider, client, sleeps = _retrying([_HTTPError(status)])
    with pytest.raises(ProviderError) as e:
        provider.chat([], [])
    assert len(client.calls) == 1 and sleeps == [] and e.value.http_status == status


def test_401_and_402_say_what_is_wrong():
    for status, word in [(401, "API key"), (402, "余额")]:
        provider, _, _ = _retrying([_HTTPError(status)])
        with pytest.raises(ProviderError, match=word):
            provider.chat([], [])


def test_timeout_is_passed_to_the_sdk_and_retries_are_not_stacked_on_the_sdk_ones(monkeypatch):
    # SDK 自己也会重试；规则要求别叠加，所以 SDK 的重试设为 0
    captured = {}

    class FakeSDK:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("openai.OpenAI", FakeSDK)

    DeepSeekProvider(api_key="k", model="deepseek-flash")

    assert captured["max_retries"] == 0 and captured["timeout"] > 0
    assert captured["base_url"] == "https://api.deepseek.com"


# --- 流式（SSE 事件）-----------------------------------------------------------------------


def _ev(type_, **kw):
    return NS(type=type_, **kw)


def _stream(*, text=("Hel", "lo"), reasoning=("think ", "more"), calls=(), final="response.completed", usage=None):
    """按真实 DeepSeek 的事件顺序拼一个流：reasoning → message → function_call → 终态。"""
    events = [_ev("response.created"), _ev("response.in_progress")]
    out = []
    if reasoning:
        events += [_ev("response.output_item.added", output_index=0, item=NS(type="reasoning", id="rs"))]
        events += [_ev("response.reasoning_text.delta", output_index=0, delta=d) for d in reasoning]
        events += [_ev("response.output_item.done", output_index=0, item=_reasoning("".join(reasoning)))]
        out.append(_reasoning("".join(reasoning)))
    if text:
        i = len(out)
        events += [_ev("response.output_item.added", output_index=i, item=NS(type="message", id="m"))]
        events += [_ev("response.output_text.delta", output_index=i, delta=d) for d in text]
        events += [_ev("response.output_item.done", output_index=i, item=_message("".join(text)))]
        out.append(_message("".join(text)))
    for n, (cid, name, parts) in enumerate(calls):
        i = len(out)
        events += [_ev("response.output_item.added", output_index=i, item=NS(type="function_call", id=f"f{n}"))]
        events += [
            _ev("response.function_call_arguments.delta", output_index=i, item_id=f"f{n}", delta=p) for p in parts
        ]
        events += [_ev("response.output_item.done", output_index=i, item=_call(cid, name, "".join(parts)))]
        out.append(_call(cid, name, "".join(parts)))
    events.append(_ev(final, response=_response(*out, status=final.split(".")[1], usage=usage)))
    return events


def test_streaming_sends_stream_true_and_calls_on_text_per_piece_without_leaking_reasoning():
    client = FakeOpenAI(events=_stream(usage=_usage(7, 2, 3)))
    provider, _ = _provider(client=client)
    pieces: list[str] = []
    conversation = [{"role": "user", "content": "hi"}]

    reply = provider.chat(conversation, [], "SYS", on_text=pieces.append)

    assert client.calls[0]["stream"] is True and "stream_options" not in client.calls[0]
    assert pieces == ["Hel", "lo"]  # 思考内容不外传
    assert reply.texts == ["Hello"] and reply.usage == Usage(7, 2, 3)
    assert conversation[-2:] == [
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "think more"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Hello"}]},
    ]


def test_streaming_assembles_arguments_per_call_and_does_not_mix_different_calls():
    events = _stream(
        text=(),
        reasoning=(),
        calls=[("c1", "read_file", ['{"path": ', '"a.txt"}']), ("c2", "list_files", ["{}"])],
    )
    provider, _ = _provider(client=FakeOpenAI(events=events))
    reply = provider.chat([], [read_file], on_text=lambda t: None)
    assert reply.tool_calls == [ToolCall("c1", "read_file", {"path": "a.txt"}), ToolCall("c2", "list_files", {})]


def test_streaming_without_on_text_is_not_streamed():
    provider, client = _provider()
    provider.chat([], [])
    assert "stream" not in client.calls[0]


def test_streaming_without_a_terminal_event_is_an_interruption_and_keeps_shown_text():
    events = _stream(final="response.completed")[:-1]  # 断流：没有终态事件
    provider, _ = _provider(client=FakeOpenAI(events=events))
    pieces: list[str] = []
    with pytest.raises(ProviderError, match="中断") as e:
        provider.chat([], [], on_text=pieces.append)
    assert pieces == ["Hel", "lo"] and e.value.partial_texts == ["Hello"]


def test_streaming_truncated_arguments_never_become_a_tool_call():
    events = _stream(text=(), reasoning=(), calls=[("c1", "read_file", ['{"path": "a'])])[:-2]  # 参数没传完就断了
    provider, _ = _provider(client=FakeOpenAI(events=events))
    with pytest.raises(ProviderError):
        provider.chat([], [read_file], on_text=lambda t: None)


@pytest.mark.parametrize("final", ["response.incomplete", "response.failed"])
def test_streaming_incomplete_or_failed_is_not_completed_and_runs_no_tools(final):
    events = _stream(calls=[("c1", "read_file", ["{}"])], final=final)
    provider, _ = _provider(client=FakeOpenAI(events=events))
    conversation: list[dict] = []
    with pytest.raises(ProviderError) as e:
        provider.chat(conversation, [read_file], on_text=lambda t: None)
    assert e.value.status == final.split(".")[1] and conversation == []


def test_streaming_reads_usage_from_the_terminal_event_and_missing_stays_none():
    provider, _ = _provider(client=FakeOpenAI(events=_stream(usage=None)))
    assert provider.chat([], [], on_text=lambda t: None).usage is None


def test_streaming_decodes_nothing_itself_so_multibyte_text_split_across_pieces_is_kept_whole():
    # SDK 已经把字节流解成事件；我们拼的是 str 片段，汉字不会被拆坏
    provider, _ = _provider(client=FakeOpenAI(events=_stream(text=("你", "好", "，世", "界"))))
    assert provider.chat([], [], on_text=lambda t: None).texts == ["你好，世界"]


# --- prune_tool_results --------------------------------------------------------------------


def _conversation_with_tool_results(n, size=1000):
    conv = [{"role": "user", "content": "go"}]
    for i in range(n):
        conv.append({"type": "reasoning", "content": [{"type": "reasoning_text", "text": f"think {i}"}]})
        conv.append({"type": "function_call", "call_id": f"c{i}", "name": "read_file", "arguments": "{}"})
        conv.append({"type": "function_call_output", "call_id": f"c{i}", "output": str(i) * size})
    return conv


def _ids(conv):
    return [(m.get("type") or m["role"], m.get("call_id")) for m in conv]


def test_prune_replaces_old_outputs_with_placeholder_keeping_structure():
    provider, _ = _provider()
    conv = _conversation_with_tool_results(4)
    before = _ids(conv)

    assert provider.prune_tool_results(conv, keep_last=2) == 2

    assert _ids(conv) == before  # 一条没少，call_id 仍然配对
    outputs = [m for m in conv if m.get("type") == "function_call_output"]
    assert "pruned" in outputs[0]["output"] and "read_file" in outputs[0]["output"]
    assert "1,000 chars" in outputs[0]["output"]
    assert outputs[2]["output"] == "2" * 1000 and outputs[3]["output"] == "3" * 1000


def test_prune_leaves_calls_and_reasoning_alone():
    provider, _ = _provider()
    conv = _conversation_with_tool_results(3)
    untouched = [dict(m) for m in conv if m.get("type") != "function_call_output"]
    provider.prune_tool_results(conv, keep_last=0)
    assert [m for m in conv if m.get("type") != "function_call_output"] == untouched


def test_prune_is_idempotent_and_skips_short_results():
    provider, _ = _provider()
    conv = _conversation_with_tool_results(3, size=10)  # 比占位符还短，换了反而更长
    assert provider.prune_tool_results(conv, keep_last=0) == 0

    conv = _conversation_with_tool_results(3)
    assert provider.prune_tool_results(conv, keep_last=0) == 3
    assert provider.prune_tool_results(conv, keep_last=0) == 0  # 已清理过的不再动


# --- 每个 call_id 恰好一条结果（规则 4/5）--------------------------------------------------


def test_duplicate_call_id_in_one_response_is_reported_not_executed_twice():
    provider, _ = _provider(_response(_call("dup"), _call("dup")))
    with pytest.raises(ProviderError, match="dup"):
        provider.chat([], [])
