"""示例插件：代码审查工作流。和默认工作流 code-review 同名（工作流 frontmatter 的 name）。

触发 eureka 的那份 JSON 是审查建议列表（每条带 file：它说的是哪个文件）。工作流分五个阶段，每个阶段一个 reconcile 方法：
  extract    提取建议：把 payload 整理成统一结构
  summarize  汇总：按类型计数、列出受影响的行、风险类排在前面
  report     写出报告：把结果写成一份 Markdown 文件，放进会话工作区
  rewrite    生成修改：读工作区里的原文件，连同审查建议交给 LangChain（DeepSeek）生成修改后的完整文件内容；只放在阶段输出里，不落盘
  apply      写出修改后的文件：校验后写成工作区里的新文件（<原名>.fixed-<运行号>.<后缀>），原文件一个字节不动

每个阶段都是幂等的：同样的输入得到同样的输出；写文件用固定的文件名覆盖，重跑不会堆出多份。

rewrite 是唯一调用模型的阶段，也是最容易失败的一步（网络、额度、模型没按要求输出），所以和写文件的 apply 分开：
失败只需要重跑 rewrite。模型的配置由主项目注入（chain_factory），插件自己不读配置、不 import Django；
LangChain 只在真正要调用模型时才导入，加载插件和前三个阶段都不依赖它。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from eureka.base import EurekaPlugin, Stage, StageContext, StageResult

# 范围展开成逐行号时的上限：payload 来自模型，不能让一个 1..10000000 的范围把内存吃光
MAX_EXPANDED_LINES = 10_000
_SEVERITY = {"QUESTION": 0, "SUGGESTION": 1}  # 风险类排在建议类前面
MAX_SOURCE_BYTES = 200 * 1024  # 发给模型的原文件上限：太大的文件既贵又容易被截断，宁可明确拒绝
# 整段回复都被围栏包住时才去掉围栏（内容里碰巧含三个反引号的不动）
_FENCE = re.compile(r"\A\s*(`{3,}|~{3,})[^\n]*\n(.*?)(?:^|\n)[ \t]*\1\s*\Z", re.DOTALL | re.MULTILINE)

SYSTEM_PROMPT = (
    "You are a careful code editor. You get one source file and a list of code review suggestions for it. "
    "Apply the suggestions that are clearly correct and safe, and leave everything else exactly as it is. "
    "Reply with the COMPLETE modified file and nothing else: no explanation, no markdown code fence."
)
HUMAN_PROMPT = "File name: {filename}\n\nReview suggestions:\n{suggestions}\n\nOriginal file:\n{original}"


class CodeReview(EurekaPlugin):
    name = "code-review"

    def __init__(self, chain_factory: Callable[[], Callable[[dict], str]] | None = None) -> None:
        super().__init__()
        # 造「调模型」的函数：输入 {filename, suggestions, original}，返回模型的文字。主项目注入带配置的版本；测试注入替身。
        # 不给就用 _default_chain：没有配置时 rewrite 阶段会明确报错，而不是让加载插件失败。
        self.chain_factory = chain_factory or _default_chain

    def stages(self) -> list[Stage]:
        return [
            Stage("extract", "提取审查建议", "reconcile_extract"),
            Stage("summarize", "汇总", "reconcile_summarize"),
            Stage("report", "写出报告文件", "reconcile_report"),
            Stage("rewrite", "生成修改（LangChain）", "reconcile_rewrite"),
            Stage("apply", "写出修改后的文件", "reconcile_apply"),
        ]

    # --- 阶段 1：提取建议 ---------------------------------------------------------

    def reconcile_extract(self, ctx: StageContext) -> StageResult:
        if not isinstance(ctx.payload, list):
            raise ValueError("审查结果应该是一个列表")
        suggestions = [
            {
                "file": item.get("file", ""),
                "start": item["startLineNum"],
                "end": item["endLineNum"],
                "type": item["type"],
                "desc": item["sugDesc"],
                "code": item["sugCode"],
                "language": item["codeLanguage"],
            }
            for item in ctx.payload
        ]
        ctx.log(f"提取到 {len(suggestions)} 条审查建议")
        return StageResult(output={"suggestions": suggestions})

    # --- 阶段 2：汇总 -------------------------------------------------------------

    def reconcile_summarize(self, ctx: StageContext) -> StageResult:
        suggestions = _previous(ctx, "extract")["suggestions"]
        by_type = dict(sorted(Counter(s["type"] for s in suggestions).items()))
        lines: set[int] = set()
        for s in suggestions:
            span = range(s["start"], min(s["end"], s["start"] + MAX_EXPANDED_LINES) + 1)
            lines.update(span)
            if len(lines) >= MAX_EXPANDED_LINES:
                break
        ordered = sorted(suggestions, key=lambda s: (_SEVERITY.get(s["type"], 9), s["start"]))
        ctx.log("汇总：" + "，".join(f"{t} {n} 条" for t, n in by_type.items()) if by_type else "汇总：没有审查建议")
        return StageResult(
            output={"total": len(suggestions), "by_type": by_type, "lines": sorted(lines)[:MAX_EXPANDED_LINES], "ordered": ordered}
        )

    # --- 阶段 3：写出报告文件 -------------------------------------------------------

    def reconcile_report(self, ctx: StageContext) -> StageResult:
        summary = _previous(ctx, "summarize")
        if ctx.workspace is None:
            raise ValueError("没有可用的会话工作区，无法写出报告文件")
        workspace = Path(ctx.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        # 文件名只由 run_id 决定：同一次运行重跑会覆盖同一个文件（幂等）；不同运行互不覆盖；名字里没有任何路径成分
        filename = f"code-review-report-{int(ctx.run_id)}.md"
        (workspace / filename).write_text(_render(summary), encoding="utf-8")
        ctx.log(f"报告已写入工作区：{filename}")
        return StageResult(output={"file": filename})

    # --- 阶段 4：生成修改（LangChain）-----------------------------------------------

    def reconcile_rewrite(self, ctx: StageContext) -> StageResult:
        suggestions = _previous(ctx, "extract")["suggestions"]
        if not suggestions:
            ctx.log("没有审查建议，无需修改")
            return StageResult(output={"file": "", "content": "", "changed": False})
        files = {item.get("file", "") for item in suggestions}
        if len(files) > 1:
            raise ValueError(f"审查建议涉及多个文件（{'、'.join(sorted(map(repr, files)))}），一次只能修改一个文件")
        filename = _safe_name(files.pop())
        original = _read_source(ctx.workspace, filename)

        ctx.log(f"读取原文件 {filename}（{len(original)} 个字符），按 {len(suggestions)} 条审查建议生成修改……")
        reply = self.chain_factory()(
            {"filename": filename, "suggestions": _format_suggestions(suggestions), "original": original}
        )
        content = _strip_fence(reply)
        if not content.strip():
            raise ValueError("模型没有返回修改后的文件内容")
        changed = content != original
        ctx.log("模型已返回修改后的内容" + ("" if changed else "，但和原文件完全一样"))
        return StageResult(output={"file": filename, "content": content, "changed": changed})

    # --- 阶段 5：写出修改后的文件 ----------------------------------------------------

    def reconcile_apply(self, ctx: StageContext) -> StageResult:
        rewrite = _previous(ctx, "rewrite")
        if ctx.workspace is None:
            raise ValueError("没有可用的会话工作区，无法写出修改后的文件")
        if not rewrite["changed"]:
            ctx.log("模型认为无需修改，没有写出新文件")
            return StageResult(output={"file": "", "source": rewrite["file"], "written": False})
        source = _safe_name(rewrite["file"])
        content = rewrite["content"]
        if not isinstance(content, str):
            raise ValueError("修改后的内容必须是文本")
        workspace = Path(ctx.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        target = _fixed_name(source, int(ctx.run_id))  # 只由原名和 run_id 决定：重跑覆盖同一份；永远不是原文件本身
        (workspace / target).write_bytes(content.encode("utf-8"))  # write_bytes：换行符原样保留，不让 Windows 再改一遍
        ctx.log(f"修改后的文件已写入工作区：{target}（原文件 {source} 未改动）；目录：{workspace}")
        return StageResult(output={"file": target, "source": source, "written": True})


def _previous(ctx: StageContext, key: str) -> dict:
    """取前面某个阶段的输出；没有就明确报错（说明是哪个阶段没跑完），而不是一个看不懂的 KeyError。"""
    if key not in ctx.previous:
        raise ValueError(f"缺少前一阶段 {key} 的输出，请先完成该阶段")
    return ctx.previous[key]


def _render(summary: dict) -> str:
    out = ["# 代码审查报告", "", f"共 {summary['total']} 条审查建议。"]
    if summary["by_type"]:
        out += ["", "## 概览", ""] + [f"- {t}：{n} 条" for t, n in summary["by_type"].items()]
    for i, s in enumerate(summary["ordered"], 1):
        where = f"第 {s['start']} 行" if s["start"] == s["end"] else f"第 {s['start']}–{s['end']} 行"
        out += ["", f"## {i}. [{s['type']}] {where}", "", s["desc"]]
        if s["code"]:
            out += ["", f"```{s['language']}", s["code"], "```"]
    return "\n".join(out) + "\n"


def _safe_name(name: str) -> str:
    """模型输出里的文件名不能信：必须是一个不含目录部分、不是 . / .. 的普通文件名，否则拒绝（绝不读 / 写工作区以外的位置）。"""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("审查建议里没有文件名，无法确定要修改哪个文件")
    if name != name.strip() or any(c in name for c in "/\\:") or name in (".", "..") or Path(name).name != name:
        raise ValueError(f"文件名不合法（只能是工作区里的一个文件名，不能带目录）：{name!r}")
    return name


def _read_source(workspace: Path | None, filename: str) -> str:
    if workspace is None:
        raise ValueError("没有可用的会话工作区，无法读取原文件")
    root = Path(workspace).resolve()
    path = (root / filename).resolve()  # resolve 会顺着符号链接走到真实位置
    if path.parent != root:
        raise ValueError(f"文件 {filename} 不在工作区里，已拒绝")  # 符号链接指到了工作区外面
    if not path.is_file():
        raise ValueError(f"工作区里找不到原文件 {filename}，请先上传它")
    raw = path.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError(f"文件 {filename} 太大（{len(raw) // 1024} KB，上限 {MAX_SOURCE_BYTES // 1024} KB），不适合整份交给模型")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"文件 {filename} 不是 UTF-8 文本，无法修改") from None


def _format_suggestions(suggestions: list[dict]) -> str:
    lines = []
    for i, s in enumerate(suggestions, 1):
        where = f"line {s['start']}" if s["start"] == s["end"] else f"lines {s['start']}-{s['end']}"
        lines.append(f"{i}. [{s['type']}] {where}: {s['desc']}")
        if s["code"]:
            lines.append(f"   suggested code: {s['code']}")
    return "\n".join(lines)


def _strip_fence(reply: str) -> str:
    """模型有时会把整个文件包进 ``` 里：整段都被围栏包住时去掉围栏；内容里碰巧含 ``` 不动。

    围栏结尾前的那个换行是「围栏的分隔」，不是文件内容；去掉围栏后补回一个结尾换行，文本文件按惯例以换行结尾，
    这样不管模型有没有在围栏前多写一个换行，得到的文件都一样。空内容保持为空（交给调用方判断「模型没有返回」）。
    """
    match = _FENCE.match(reply)
    if not match:
        return reply
    body = match.group(2)
    if body.strip() and not body.endswith("\n"):
        body += "\n"
    return body


def _fixed_name(source: str, run_id: int) -> str:
    """models.py → models.fixed-3.py；没有后缀的 Makefile → Makefile.fixed-3。"""
    stem, dot, suffix = source.rpartition(".")
    return f"{stem}.fixed-{run_id}.{suffix}" if dot and stem else f"{source}.fixed-{run_id}"


def _default_chain() -> Callable[[dict], str]:
    raise ValueError("没有配置 DeepSeek 模型（config.ini 的 [eureka] llm_api_key），无法调用 LangChain")


def langchain_chain(api_key: str, model: str, base_url: str, reasoning_effort: str = "low", timeout: float = 50.0):
    """用 LangChain 造「调 DeepSeek」的函数。DeepSeek 走原生 Responses API（use_responses_api=True），不走 /chat/completions。

    LangChain 在这里才导入：加载插件、跑前三个阶段都不需要它；没装也不会让整个服务起不来。
    """
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,  # 重试由 runner 的阶段超时和用户重跑负责，不在这里叠加
        use_responses_api=True,
        reasoning={"effort": reasoning_effort},
    )
    chain = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", HUMAN_PROMPT)]) | llm | StrOutputParser()
    return chain.invoke  # 变量的值不会被二次当成模板解析：文件内容里常有的花括号是安全的


PLUGIN = CodeReview  # 注册表按这个名字取插件类
