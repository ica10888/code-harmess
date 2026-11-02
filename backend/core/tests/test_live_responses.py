"""Live 冒烟测试（Responses API）：调用真实 DeepSeek API，验证 fake 测试证明不了的东西——线上格式。

    uv run --env-file .env pytest -m live backend/core/tests/test_live_responses.py     # 要 key、要花钱

只断言「行为」（流式有没有分段、工具调用能不能配对回灌、模型和强度有没有真的生效），不比对原文。
"""

from __future__ import annotations

import os

import pytest
from providers import EFFORTS, MODELS, DeepSeekProvider
from tools import read_file

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="需要 DEEPSEEK_API_KEY"),
]


def _ask(provider, text, **kw):
    conversation = [{"role": "user", "content": text}]
    return conversation, provider.chat(conversation, [], **kw)


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("effort", EFFORTS)
def test_every_model_and_effort_combination_is_accepted_by_the_real_api(model, effort):
    _, reply = _ask(DeepSeekProvider(), "1+1=? 只回答数字", model=model, effort=effort)
    assert "2" in "".join(reply.texts)
    assert reply.usage is not None and reply.usage.input_tokens > 0


def test_effort_none_turns_thinking_off_and_the_others_turn_it_on():
    provider = DeepSeekProvider()
    off, _ = _ask(provider, "17*23=? 只回答数字", effort="none")
    on, _ = _ask(provider, "17*23=? 只回答数字", effort="high")
    assert not any(i.get("type") == "reasoning" for i in off)
    assert any(i.get("type") == "reasoning" for i in on)


def test_streaming_delivers_text_in_pieces_and_matches_the_final_reply():
    pieces: list[str] = []
    conversation = [{"role": "user", "content": "用三句话介绍一下春天。"}]
    reply = DeepSeekProvider().chat(conversation, [], on_text=pieces.append)
    assert len(pieces) > 3 and "".join(pieces) == "".join(reply.texts)


def test_tool_call_round_trip_pairs_call_ids_and_keeps_reasoning(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("SECRET-4242", encoding="utf-8")
    provider = DeepSeekProvider()
    conversation = [{"role": "user", "content": f"用工具读 {f}，告诉我里面的内容。"}]

    first = provider.chat(conversation, [read_file], effort="low")
    assert first.tool_calls and first.tool_calls[0].name == "read_file"
    conversation.extend(provider.tool_results([_run(c) for c in first.tool_calls]))

    second = provider.chat(conversation, [read_file], effort="low")  # 历史里已有 reasoning + function_call
    assert "SECRET-4242" in "".join(second.texts)


def test_model_and_effort_can_change_in_the_middle_of_a_conversation():
    provider = DeepSeekProvider()
    conversation = [{"role": "user", "content": "记住暗号：BLUE-42。只回答「好」。"}]
    provider.chat(conversation, [], model="deepseek-flash", effort="high")
    conversation.append({"role": "user", "content": "暗号是什么？"})
    reply = provider.chat(conversation, [], model="deepseek-v4-pro", effort="none")
    assert "BLUE-42" in "".join(reply.texts)


def _run(call):
    from providers import ToolResult

    return ToolResult(call.id, read_file.run(call.input))
