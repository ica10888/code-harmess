"""适配层：用 backend/core 的 Agent 处理网页里的一问一答。

循环本身（问模型 → 执行工具 → 回灌 → 修剪上下文）全在 core 的 Agent.respond 里，这里只管三件事：
按 settings 选工具、拼网页用的系统提示词、把 ChatSession 里存的对话交给 Agent 再存回去。
"""

import json
import os
from datetime import datetime

from django.conf import settings

# core 的模块用的是顶层 import（import tools、from providers import ...），所以要把它的 src 放进 sys.path。
# 这一步在 settings.py 里做；这里直接 import 即可。
from agent import DEFAULT_SESSION, Agent  # noqa: E402
from prompt import PromptContext, build_system_prompt  # noqa: E402
from providers import DeepSeekProvider  # noqa: E402
from tools import ALL_TOOLS  # noqa: E402

EMPTY_REPLY = "（模型没有返回文字）"
PAUSED_NOTICE = "已连续 {rounds} 轮工具调用，先暂停；回复「继续」让我接着做。"

_provider = None


def get_provider():
    """第一次用到时才创建（没配 key 时，其他接口照常能用）。测试里替换它。

    模型、key、地址都读自 Django settings（来自 backend/config.ini 的 [deepseek]），不读环境变量。
    """
    global _provider
    if _provider is None:
        key = settings.DEEPSEEK_API_KEY
        if not key or key.startswith("sk-your"):
            raise RuntimeError("请在 backend/config.ini 的 [deepseek] 里填上 api_key（https://platform.deepseek.com）")
        _provider = DeepSeekProvider(model=settings.DEEPSEEK_MODEL, api_key=key, base_url=settings.DEEPSEEK_BASE_URL)
    return _provider


def _web_role():
    """网页聊天里的角色说明。要写明真实模型：core 的默认提示词是「coding agent」，
    模型拿它自我介绍时会自称 Claude；说清楚它是谁，它才不会冒充别家。"""
    return (
        "You are a coding assistant inside a web chat page. The user types in a text box and reads your replies there.\n"
        f"You are powered by DeepSeek (model: {settings.DEEPSEEK_MODEL}). If asked what model you are or who made you, "
        "say so; never claim to be Claude, GPT or any other model."
    )


def _agent(conversation: list[dict]) -> Agent:
    """造一个只服务这一次请求的 Agent，对话是这个会话已有的。

    网页里没人能点「允许」，所以 approve 一律拒绝：需要审批的工具（run_bash）即使加进 HARNESS_TOOLS 也跑不起来。
    get_user_input 不会被用到（respond 不读输入），传个返回 None 的占位，免得它去建终端输入框。
    """
    tools = [t for t in ALL_TOOLS if t.name in settings.HARNESS_TOOLS]
    system = build_system_prompt(
        PromptContext(
            working_directory=os.getcwd(),
            tool_names=[t.name for t in tools],
            today=datetime.now().astimezone().date().isoformat(),
            persona=_web_role(),
        )
    )
    agent = Agent(
        get_provider(),
        tools,
        get_user_input=lambda: None,
        system=system,
        approve=lambda call: False,
        max_tool_rounds=settings.HARNESS_MAX_TOOL_ROUNDS,
        context_budget=settings.HARNESS_CONTEXT_BUDGET,
        keep_tool_results=settings.HARNESS_KEEP_TOOL_RESULTS,
    )
    agent.sessions[DEFAULT_SESSION].conversation[:] = conversation
    return agent


def run_turn(session, user_text: str) -> str:
    """处理用户的一句话，返回要展示给用户的回复。

    Agent.respond 在副本上推进对话，整轮成功才写回；中途抛异常时对话原样不动，所以这里也只在成功后才保存。
    """
    agent = _agent(session.conversation)
    answer = agent.respond(user_text)

    session.conversation = json.loads(json.dumps(agent.current.conversation))  # 确保能存进 JSONField
    session.save(update_fields=["conversation"])
    texts = [*answer.texts, PAUSED_NOTICE.format(rounds=settings.HARNESS_MAX_TOOL_ROUNDS)] if answer.paused else answer.texts
    return "\n\n".join(texts) or EMPTY_REPLY
