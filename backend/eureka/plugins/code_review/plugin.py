"""示例插件：代码审查工作流。和默认工作流 code-review 同名（工作流 frontmatter 的 name）。

触发 eureka 的那份 JSON 是审查建议列表。工作流分三个阶段，每个阶段一个 reconcile 方法：
  extract    提取建议：把 payload 整理成统一结构
  summarize  汇总：按类型计数、列出受影响的行、风险类排在前面
  report     写出报告：把结果写成一份 Markdown 文件，放进会话工作区

每个阶段都是幂等的：同样的输入得到同样的输出；写文件用固定的文件名覆盖，重跑不会堆出多份。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from eureka.base import EurekaPlugin, Stage, StageContext, StageResult

# 范围展开成逐行号时的上限：payload 来自模型，不能让一个 1..10000000 的范围把内存吃光
MAX_EXPANDED_LINES = 10_000
_SEVERITY = {"QUESTION": 0, "SUGGESTION": 1}  # 风险类排在建议类前面


class CodeReview(EurekaPlugin):
    name = "code-review"

    def stages(self) -> list[Stage]:
        return [
            Stage("extract", "提取审查建议", "reconcile_extract"),
            Stage("summarize", "汇总", "reconcile_summarize"),
            Stage("report", "写出报告文件", "reconcile_report"),
        ]

    # --- 阶段 1：提取建议 ---------------------------------------------------------

    def reconcile_extract(self, ctx: StageContext) -> StageResult:
        if not isinstance(ctx.payload, list):
            raise ValueError("审查结果应该是一个列表")
        suggestions = [
            {
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


PLUGIN = CodeReview  # 注册表按这个名字取插件类
