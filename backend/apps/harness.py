"""适配层：用 backend/core 的 Agent 处理网页里的一问一答。

循环本身（问模型 → 执行工具 → 回灌 → 修剪上下文）全在 core 的 Agent.respond 里，这里只管三件事：
按 settings 选工具、拼网页用的系统提示词、把 ChatSession 里存的对话交给 Agent 再存回去。
"""

import json
import logging
import os
import queue
import threading
from datetime import datetime

from django.conf import settings

# core 的模块用的是顶层 import（import tools、from providers import ...），所以要把它的 src 放进 sys.path。
# 这一步在 settings.py 里做；这里直接 import 即可。
from agent import DEFAULT_SESSION, Agent  # noqa: E402
from prompt import PromptContext, build_system_prompt  # noqa: E402
from providers import DeepSeekProvider  # noqa: E402
from skills import Skill, load_skill_file  # noqa: E402
from tools import workspace_tools  # noqa: E402

logger = logging.getLogger(__name__)

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


def _web_role(can_edit: bool, model: str | None = None):
    """网页聊天里的角色说明。要写明真实模型：core 的默认提示词是「coding agent」，
    模型拿它自我介绍时会自称 Claude；说清楚它是谁，它才不会冒充别家。"""
    role = (
        "You are a coding assistant inside a web chat page. The user types in a text box and reads your replies there.\n"
        f"You are powered by DeepSeek (model: {model or settings.DEEPSEEK_MODEL}). If asked what model you are or who made you, "
        "say so; never claim to be Claude, GPT or any other model.\n"
        "Your tools work inside a private workspace folder that belongs to this conversation. "
        "Always use paths relative to the workspace (e.g. `notes/a.txt`, never absolute paths or `..`)."
    )
    if can_edit:  # 只读模式下没有这个工具，就别提，免得模型想去调
        role += " The workspace starts empty: files you create are kept there and you can list and read them later."
    return role


def workspace_dir(session) -> str:
    """这个会话的工作区：<存储路径>/input/<会话id>/file。工具第一次用到时才会创建它。"""
    return os.path.join(settings.HARNESS_STORAGE_DIR, "input", str(session.id), "file")


def skills_dir(session) -> str:
    """这个会话的插件目录：<存储路径>/input/<会话id>/skills，和 file/ 同级。每个插件是其中的 <名称>/SKILL.md。"""
    return os.path.join(settings.HARNESS_STORAGE_DIR, "input", str(session.id), "skills")


def workflow_dir(session) -> str:
    """这个会话的工作流目录：<存储路径>/input/<会话id>/workflow，和 file/、skills/ 同级。每个工作流是其中的 <文件名>.md。"""
    return os.path.join(settings.HARNESS_STORAGE_DIR, "input", str(session.id), "workflow")


def candidate_skills(session) -> list[Skill]:
    """「先选后载」的候选：默认插件和这个会话自己的插件里，core 读得了的（读不了 / 格式不对的跳过）。

    用不用、用哪个，由 core 的 skill_selector 在每句话之前选；没有用户勾选这回事。
    """
    from . import services  # 避免循环导入：services 也 import 了 harness

    skills = []
    for path in services.all_plugin_paths(session):
        try:
            skills.append(load_skill_file(path))
        except (OSError, ValueError):
            continue
    return skills


def _agent(session, model: str | None = None) -> Agent:
    """造一个只服务这一次请求的 Agent，对话是这个会话已有的。

    读写文件的工具被限制在这个会话自己的工作区里（tools.workspace_tools），模型碰不到别的会话和项目文件。
    run_bash 也提供（工作区版，不审批，工作目录是工作区）：它能跑任意命令，限不住目录，等于信任模型和用户；
    网页里没人能点「允许」，其余要审批的工具 approve 一律拒绝作为兜底。
    get_user_input 不会被用到（respond 不读输入），传个返回 None 的占位，免得它去建终端输入框。
    """
    allowed = [t for t in workspace_tools(workspace_dir(session)) if t.name in settings.HARNESS_TOOLS]
    system = build_system_prompt(
        PromptContext(
            # 不给模型真实的服务器路径；它看到的工作目录就叫 workspace，所有路径相对于它
            working_directory="workspace",
            tool_names=[t.name for t in allowed],
            today=datetime.now().astimezone().date().isoformat(),
            persona=_web_role(can_edit=any(t.name == "edit_file" for t in allowed), model=model),
        )
    )
    agent = Agent(
        get_provider(),
        allowed,
        get_user_input=lambda: None,
        system=system,
        approve=lambda call: False,
        max_tool_rounds=settings.HARNESS_MAX_TOOL_ROUNDS,
        context_budget=settings.HARNESS_CONTEXT_BUDGET,
        keep_tool_results=settings.HARNESS_KEEP_TOOL_RESULTS,
        skills=candidate_skills(session),
    )
    agent.sessions[DEFAULT_SESSION].conversation[:] = session.conversation
    return agent


def _finish(session, agent: Agent, answer) -> str:
    """整轮成功后：保存对话，拼出要展示给用户的完整回复（run_turn 和 stream_turn 共用）。

    skill_selector 这一轮选中的 skill 名顺手记在 session.selected_skills 上（不入库的临时属性），
    由 services.save_assistant_message 取走并存成 SkillSelection；返回类型不变，已有的调用方和测试都不用动。
    """
    session.conversation = json.loads(json.dumps(agent.current.conversation))  # 确保能存进 JSONField
    session.save(update_fields=["conversation"])
    session.selected_skills = list(answer.selected)
    texts = [*answer.texts, PAUSED_NOTICE.format(rounds=settings.HARNESS_MAX_TOOL_ROUNDS)] if answer.paused else answer.texts
    return "\n\n".join(texts) or EMPTY_REPLY


def workflow_context_for(session):
    """core 选中 skill 之后的回调：把同名工作流的输出格式（schema）作为额外的一条消息放进对话。

    返回 names -> 文字 | None。对话里已经有这个工作流的 <workflow name="..."> 块就不再放（每句话重复塞全文会占满上下文）。
    """
    from . import workflows  # 避免循环导入

    def context(names: list[str]) -> str | None:
        already = "\n".join(str(m.get("content")) for m in session.conversation if m.get("role") == "user")
        return workflows.workflow_message(session, names, already)

    return context


def run_turn(session, user_text: str, model: str | None = None, effort: str | None = None) -> str:
    """处理用户的一句话，返回要展示给用户的回复。model / effort 是本次请求选的模型和推理强度，不给就用默认。

    Agent.respond 在副本上推进对话，整轮成功才写回；中途抛异常时对话原样不动，所以这里也只在成功后才保存。
    """
    agent = _agent(session, model)
    return _finish(
        session,
        agent,
        agent.respond(user_text, model=model, effort=effort, extra_context=workflow_context_for(session)),
    )


def stream_turn(session, user_text: str, model: str | None = None, effort: str | None = None):
    """和 run_turn 一样处理一句话，但边生成边吐：依次产出 ("delta", 一段文字) …… 最后产出 ("done", 完整回复)。

    Agent.respond 是同步的，所以放在一个线程里跑，文字片段经队列交给这个生成器。
    失败时在生成器里重新抛出异常（对话没动，调用方决定怎么告诉用户）。
    "done" 的完整回复可能比流出去的文字多一句暂停提示，前端应以它为准。
    """
    pieces: queue.Queue = queue.Queue()
    outcome: dict = {}
    agent = _agent(session, model)
    extra_context = workflow_context_for(session)

    def work():
        try:
            outcome["answer"] = agent.respond(
                user_text,
                on_text=lambda t: pieces.put(("delta", t)),
                model=model,
                effort=effort,
                extra_context=extra_context,
            )
        except Exception as e:  # 交给生成器所在的线程去抛
            outcome["error"] = e
        finally:
            pieces.put(None)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    while (item := pieces.get()) is not None:
        yield item
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    yield "done", _finish(session, agent, outcome["answer"])
