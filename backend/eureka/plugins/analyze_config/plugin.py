"""示例插件：分析配置文件工作流。和默认工作流 analyze-config 同名（工作流 frontmatter 的 name）。

触发 eureka 的那份 JSON 是从配置文件里抽取出的配置项列表（mysql / redis / kafka / zookeeper / elasticSearch）。
工作流分三个阶段，每个阶段一个 reconcile 方法：
  extract    提取配置项：补齐缺失的字段，记录哪些信息缺失
  classify   按类型整理：分组、计数、标出带密码的和集群地址
  write      写入工作区：写出 JSON 和 Markdown 两个文件，并告诉用户目录（工作流文件里写的就是「写入到工作目录，并告诉我目录」）

每个阶段都是幂等的：同样的输入得到同样的输出；写文件用只由 run_id 决定的固定文件名覆盖，重跑不会堆出多份。

安全：配置项里常带密码。写进工作区的文件模型的文件工具读得到，所以落盘时一律把非空密码换成 ***，
只留「有没有密码」这个事实；阶段输出里最后一个阶段（会被前端看到）也不含明文。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from eureka.base import EurekaPlugin, Stage, StageContext, StageResult

# 每种类型有哪些字段（和 analyze-config 工作流的 schema 一致）；也是输出时的固定顺序
FIELDS: dict[str, list[str]] = {
    "mysql": ["address", "port", "database", "username", "password"],
    "redis": ["address", "name", "password"],
    "kafka": ["address", "password", "group", "topic"],
    "zookeeper": ["address", "password"],
    "elasticSearch": ["address", "password"],
}
MASK = "***"
_SECRET_FIELDS = {"password"}


class AnalyzeConfig(EurekaPlugin):
    name = "analyze-config"

    def stages(self) -> list[Stage]:
        return [
            Stage("extract", "提取配置项", "reconcile_extract"),
            Stage("classify", "按类型整理", "reconcile_classify"),
            Stage("write", "写入工作区", "reconcile_write"),
        ]

    # --- 阶段 1：提取配置项 -------------------------------------------------------

    def reconcile_extract(self, ctx: StageContext) -> StageResult:
        if not isinstance(ctx.payload, list):
            raise ValueError("配置项结果应该是一个列表")
        items: list[dict] = []
        missing: list[dict] = []
        for index, raw in enumerate(ctx.payload):
            kind = raw.get("type") if isinstance(raw, dict) else None
            if kind not in FIELDS:
                raise ValueError(f"第 {index + 1} 项的类型 {kind!r} 不认识（支持：{', '.join(FIELDS)}）")
            item = {"type": kind, **{field: str(raw.get(field, "")) for field in FIELDS[kind]}}  # 新字典，不改 payload
            absent = [field for field in FIELDS[kind] if field not in raw]
            if absent:
                missing.append({"index": index, "type": kind, "fields": absent})
            items.append(item)
        ctx.log(f"提取到 {len(items)} 个配置项" + (f"，其中 {len(missing)} 个有缺失的信息" if missing else ""))
        return StageResult(output={"items": items, "missing": missing})

    # --- 阶段 2：按类型整理 -------------------------------------------------------

    def reconcile_classify(self, ctx: StageContext) -> StageResult:
        items = _previous(ctx, "extract")["items"]
        grouped: dict[str, list[dict]] = defaultdict(list)
        for item in items:
            grouped[item["type"]].append(item)
        by_type = {kind: grouped[kind] for kind in FIELDS if kind in grouped}  # 固定顺序，没有的类型不出现
        counts = {kind: len(rows) for kind, rows in by_type.items()}
        clusters = [
            {"index": i, "type": item["type"], "nodes": _nodes(item["address"])}
            for i, item in enumerate(items)
            if _nodes(item["address"]) > 1
        ]
        with_password = sum(1 for item in items if item.get("password"))
        for kind, n in counts.items():
            ctx.log(f"{kind}：{n} 个")
        return StageResult(output={"counts": counts, "by_type": by_type, "with_password": with_password, "clusters": clusters})

    # --- 阶段 3：写入工作区 -------------------------------------------------------

    def reconcile_write(self, ctx: StageContext) -> StageResult:
        classified = _previous(ctx, "classify")
        extract = ctx.previous.get("extract", {"items": [], "missing": []})
        if ctx.workspace is None:
            raise ValueError("没有可用的会话工作区，无法写入文件")
        workspace = Path(ctx.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        run = int(ctx.run_id)  # 文件名只由 run_id 决定：重跑覆盖同一份；不同运行互不覆盖；名字里没有任何路径成分
        json_name, md_name = f"analyze-config-{run}.json", f"analyze-config-{run}.md"

        masked_items = [_mask(item) for item in extract["items"]]
        document = {"items": masked_items, "counts": classified["counts"], "missing": extract["missing"], "clusters": classified["clusters"]}
        (workspace / json_name).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        (workspace / md_name).write_text(_render(masked_items, classified, extract["missing"]), encoding="utf-8")

        ctx.log(f"已写入工作目录：{workspace}")
        ctx.log(f"文件：{json_name}、{md_name}")
        return StageResult(output={"directory": str(workspace), "files": [json_name, md_name]})


def _previous(ctx: StageContext, key: str) -> dict:
    """取前面某个阶段的输出；没有就明确报错（说明是哪个阶段没跑完），而不是一个看不懂的 KeyError。"""
    if key not in ctx.previous:
        raise ValueError(f"缺少前一阶段 {key} 的输出，请先完成该阶段")
    return ctx.previous[key]


def _nodes(address: str) -> int:
    """地址里有几个节点（逗号分隔）：大于 1 就是集群。"""
    return len([part for part in address.split(",") if part.strip()])


def _mask(item: dict) -> dict:
    """落盘用的副本：非空的密码换成 ***，空的仍然是空（保留「有没有密码」这个事实，不留明文）。"""
    return {k: (MASK if k in _SECRET_FIELDS and v else v) for k, v in item.items()}


def _line(text: str) -> str:
    """配置值放进 Markdown 前压成一行：值里的换行不能被用来伪造出新的标题或代码块。"""
    return " ".join(str(text).split())


def _render(items: list[dict], classified: dict, missing: list[dict]) -> str:
    out = ["# 配置文件分析报告", ""]
    if not items:
        return "\n".join(out + ["没有提取到任何配置项。", ""])
    out.append(f"共 {len(items)} 个配置项：" + "，".join(f"{kind} {n} 个" for kind, n in classified["counts"].items()) + "。")
    if classified["with_password"]:
        out.append(f"其中 {classified['with_password']} 个带有密码（报告里已隐藏）。")
    for kind in FIELDS:
        rows = [item for item in items if item["type"] == kind]
        if not rows:
            continue
        out += ["", f"## {kind}"]
        for number, item in enumerate(rows, 1):
            out += ["", f"### {kind} #{number}", ""]
            out += [f"- {field}：{_line(item[field]) or '（空）'}" for field in FIELDS[kind]]
    if missing:
        out += ["", "## 缺失的信息", ""]
        out += [f"- 第 {m['index'] + 1} 项（{m['type']}）：{'、'.join(m['fields'])}" for m in missing]
    return "\n".join(out) + "\n"


PLUGIN = AnalyzeConfig  # 注册表按这个名字取插件类
