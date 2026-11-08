"""skill_selector：正式请求之前，先问一次模型「这次要加载哪些 skill」，只返回 SkillRef(name, path)。

核心链路上的一个独立模块：不依赖后端的插件、工作流；provider 只用公开的 chat 接口。
input 的排法照 docs/codex_skill_loading_report_v5.md 的伪请求：

  ① 注入点①（历史）：AvailableSkillsInstructions，role=developer，<skills_instructions> 包着目录（每个 skill 一行）
  ② 用户输入，role=user
  ③ 注入点②（历史）：SkillInstructions，role=user，<skill> 包着正文，写在用户消息之后；
     只有用户用 $名字 点名、且目录里有的 skill 才有

模型只回答名字；path 一律从目录里取，不信模型给的路径。这里只「选」，不读正文进正式对话——加载是以后的事。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from providers import Provider
from skills import Skill

SELECTOR_SYSTEM = (
    "You pick which skills should be loaded for the user's request. "
    "The available skills are listed in the <skills_instructions> message. "
    'Reply with JSON only, in the form {"skills": ["<skill name>", ...]}. '
    "Use only names from the list; return an empty list if no skill is needed."
)
SELECTOR_EFFORT = "none"  # 只是选名字，不需要思考

_MENTION = re.compile(r"\$([a-z0-9]+(?:-[a-z0-9]+)*)")  # $commit-msg


@dataclass(frozen=True)
class SkillRef:
    name: str
    path: str  # SKILL.md 的路径，取自目录


def _catalog(skills: list[Skill]) -> str:
    lines = [f"- {s.name}: {' '.join(s.description.split())} (file: {s.path})" for s in skills]
    return "<skills_instructions>\n## Skills\n### Available skills\n" + "\n".join(lines) + "\n</skills_instructions>"


def _skill_body(skill: Skill) -> str | None:
    try:
        with open(skill.path, encoding="utf-8", errors="replace") as f:
            contents = f.read()
    except OSError:
        return None
    return f"<skill>\n<name>{skill.name}</name>\n<path>{skill.path}</path>\n{contents}\n</skill>"


def build_selection_input(user_input: str, skills: list[Skill]) -> list[dict]:
    """目录 → 用户输入 → 被点名 skill 的正文（读不了的跳过）。"""
    items = [
        {"role": "developer", "content": _catalog(skills)},
        {"role": "user", "content": user_input},
    ]
    by_name = {s.name: s for s in skills}
    for name in dict.fromkeys(_MENTION.findall(user_input)):  # 去重并保持顺序
        body = _skill_body(by_name[name]) if name in by_name else None
        if body:
            items.append({"role": "user", "content": body})
    return items


def _parse_names(text: str) -> list[str]:
    """从模型回复里取出 {"skills": [...]}（可以包在说明文字或代码块里）；取不到就是空。"""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        names = value.get("skills") if isinstance(value, dict) else None
        if isinstance(names, list):
            return [n["name"] if isinstance(n, dict) and isinstance(n.get("name"), str) else n for n in names]
    return []


def select_skills(provider: Provider, user_input: str, skills: list[Skill]) -> list[SkillRef]:
    """请求一次模型，返回要加载的 skill（名称、路径）。没有可用 skill 就不发请求。"""
    if not skills:
        return []
    conversation = build_selection_input(user_input, skills)
    reply = provider.chat(conversation, [], system=SELECTOR_SYSTEM, effort=SELECTOR_EFFORT)
    by_name = {s.name: s for s in skills}
    refs: dict[str, SkillRef] = {}
    for name in _parse_names("\n".join(reply.texts)):
        if isinstance(name, str) and name in by_name and name not in refs:
            refs[name] = SkillRef(name, by_name[name].path)
    return list(refs.values())
