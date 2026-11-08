# OpenAI Codex Skill 加载机制源码分析报告（意图分析链路）
> **结论依据**：附件文章讲的是 **Claude Code** 的 Skill 系统，不是 Codex。**本报告以 Codex 源码为准。** 文章内容只作为【文章】/【差异】对照保留，不作为结论依据。
> **源码版本**：`https://github.com/openai/codex` main 分支浅克隆，commit `c9b4267`（2026-11-08），下文路径都相对 `codex-rs/`。
> **范围**：只看意图分析链路，也就是用户输入 → 识别 Skill → 注入上下文 → 请求 Responses API。不涉及权限、沙箱、Guardian、MCP、文件存储。
> **资料**：只使用源码，以及用户提供的文章正文（https://zhuanlan.zhihu.com/p/2040565661118750860）；没有参考其他文档、博客或搜索摘要。
> **标注**：【推断】表示源码还不足以完全确认的结论。

---

## 1. 总体结论（源码确认）

Codex 的 Skill 分**两段**注入。两段都是 Responses 请求 `input` 数组里的消息，请求体没有单独的 `instructions` 字段（见第 5 节）。

| 阶段 | 注入内容 | 片段类型 / 标记 | 注入时机 | 源码位置 |
|---|---|---|---|---|
| ① 目录（元数据） | 所有可用 Skill 的 name、description、定位符；不含正文 | `AvailableSkillsInstructions`：role=`developer`，kind=`skills.catalog`，用 `<skills_instructions>…</skills_instructions>` 包裹 | 每个 turn 开始时，由 `record_context_updates_and_set_reference_context_item` 写入。需要全量注入时随初始上下文一起写入；否则只写 world state 的差异 | `ext/skills/src/fragments.rs`、`render.rs:568`、`catalog_delta.rs:384`、`core/src/session/mod.rs:5465` |
| ② 正文 | 被**显式点名**的 Skill 的 `SKILL.md` 原文 | `SkillInstructions`：role=`user`，kind=`skills.selected_skill_instructions`，用 `<skill>…</skill>` 包裹 | 同一 turn 内，用户输入写入历史**之后**、首次采样请求**之前** | `ext/skills/src/host_prompt.rs`、`core/src/session/turn.rs:399, 498` |

意图分析分为两条路径。

**显式路径（由代码判定）**
- `codex_skills::collect_explicit_skill_mentions`（`skills/src/selection.rs:44`）解析三类点名：
  - `$skill-name`（`skills/src/mentions.rs:41`，`TOOL_MENTION_SIGIL = '$'`）；
  - `skill://…` 或指向 `SKILL.md` 的路径；
  - 结构化的 Skill 输入。
- 命中的 Skill 由 `load_skill_prompts` 读取正文并注入。
- 【文章】点名方式是 `/skill-name`。【差异】Codex 用 `$` 及上面的路径或结构化输入；同名有歧义、已禁用或与 connector 名称冲突时会跳过。

**隐式路径（由模型判定）**
- 没有点名时，Codex **不注入正文**，模型只能看到目录中的 description。
- 模型如果决定使用某个 Skill，会通过 shell 命令读取 `SKILL.md`。这时 `core/src/tools/handlers/unified_exec/exec_command.rs:168` 调用 `maybe_emit_implicit_skill_invocation`（`core/src/skills.rs:237`），后者再调用 `detect_implicit_skill_invocation` 识别这次读取，并发出调用事件、记录使用情况。正文作为该命令的输出返回给模型。
- 【文章】按语义相似度打置信度分，超过约 0.65 的阈值就自动加载。【差异】Codex 源码中没有相似度、置信度或阈值的实现；代码只用 `SkillPolicy.allow_implicit_invocation`（`skills/src/model.rs`）控制某个 Skill 是否允许隐式使用【它在目录渲染或识别中具体如何生效，本次没有逐行核对，为推断】。

---

## 2. 请求链路（逐段复核后的调用顺序）

```
用户输入 "用 $commit-msg 帮我写提交信息"
  │
  ▼
core/src/tasks/regular.rs:151        run_turns → run_turn(...)
  ▼
core/src/session/turn.rs:217         run_turn
  │
  │ (A) 约 L366–371  record_context_updates_and_set_reference_context_item(first_step_context)
  │        └─ session/mod.rs:5465
  │            build_world_state_for_step(step_ctx, should_inject_full_context)
  │            ├─ 需要全量（首个 turn / 历史被清空或压缩后）
  │            │     → build_initial_context_with_world_state      ★ 注入点①：全量目录
  │            └─ 否则 → merge_world_state_updates(差异)          ★ 注入点①'：增量目录（catalog_delta.rs）
  │            → set_world_state_baseline(snapshot)
  │
  │ (B) L399  build_skills_and_plugins(sess, step_ctx, user_input, ...)   // 定义在 L1260
  │        ├─ HostSkillsSnapshot::for_step(...)                  ext/skills/src/host_snapshot.rs:21
  │        ├─ build_skill_name_counts(...)                       skills/src/name_counts.rs
  │        ├─ collect_explicit_skill_mentions(user_input, …)     ★ 显式意图识别
  │        ├─ load_skill_prompts(&mentioned_skills, …)           ext/skills/src/host_prompt.rs:70
  │        ├─ emit_explicit_skill_invocations(...)               core/src/skills.rs:145
  │        ├─ 过滤已经通过 InjectedHostSkillPrompts 注入的路径    L1397–1408
  │        └─ 返回 (injection_items, explicitly_enabled_connectors)   // 只生成，暂不写入
  │
  │ (C) L455 起  run_hooks_and_record_inputs(..., &input, PersistContext::TurnStart)   // 定义在 L1038
  │        └─ 对每条输入调用 record_pending_input → 用户消息写入历史
  │
  │ (D) L498–504  for item in injection_items { sess.record_conversation_items(...) }
  │        ★ 注入点②：<skill> 正文写入历史，位于用户消息之后
  │
  ▼  L647  run_sampling_request(...)                              // 定义在 L1891
  │        history = sess.clone_history()                         L1941
  │        prompt_input = history.for_prompt(input_modalities, …) L1949
  │        prompt = build_prompt(prompt_input, step_ctx, base_instructions, incremental_tools)   L1957 / 定义在 L1842
  ▼  L2008  try_run_sampling_request(...)                         // 定义在 L2888
  │        client_session.stream(prompt, model_info, …, effort, reasoning_summary, service_tier, responses_metadata, …)   L2996
  ▼
core/src/client.rs  ModelClientSession::stream                   // 定义在 L2584
  │        responses_websocket_enabled() ? stream_responses_websocket : stream_responses_http
  │        两条分支都会调用 build_responses_request(prompt, …)       L2028（HTTP）/ L2218（WebSocket）
  ▼
core/src/client.rs:1162  build_responses_request → ResponsesApiRequest   // 结构体定义在 codex-api/src/common.rs:287
  ▼
发送到 /responses（codex-api/src/lib.rs: RESPONSES_PATH），以流式方式接收结果
```

- 【文章】匹配成功后从 Level 1 升级到 Level 2，把正文注入上下文。
  - 与源码一致：正文确实在模型推理之前注入。
  - 【差异】Codex 只有显式点名才会走 (B)(D) 注入正文；隐式使用不经过这一步。

---

## 3. 核心方法一览（源码位置）

| 层次 | 文件:行 | 方法 / 结构 | 作用 |
|---|---|---|---|
| 解析 | `skills/src/parser.rs:44` | `parse_skill_frontmatter_metadata` → `ParsedSkillFrontmatter{name, description, short_description}` | 解析并校验 SKILL.md 的 frontmatter |
| 解析 | `skills/src/metadata.rs:27` | `parse_skill_metadata`（解析 interface / dependencies / policy） | 解析其余元数据 |
| 模型 | `skills/src/model.rs` | `SkillMetadata{name, description, path_to_skills_md, scope, …}`、`SkillPolicy{allow_implicit_invocation, products}` | Skill 元数据 |
| 系统 Skill | `skills/src/lib.rs:80` | `install_system_skills`（写入 `$CODEX_HOME/skills/.system`） | 安装内置 Skill |
| 加载入口 | `core/src/skills.rs:37, 50, 91` | `skills_load_input_from_config`、`skills_load_input_for_environments`、`host_skills_for_step` | 计算 Skill 根目录，生成本 step 的 Skill 快照 |
| 快照 | `ext/skills/src/host_snapshot.rs:21` | `HostSkillsSnapshot::for_step` | 取出 Skill 加载结果 |
| 目录渲染 | `ext/skills/src/render.rs:583, 594` | `render_available_skills`、`render_prepared_skill_catalogs` | 在 token 预算内渲染目录 |
| 目录正文 | `ext/skills/src/catalog_prompt.rs:100` | `render_available_skills_body` | 拼接 `## Skills` → intro → `### Skill roots` → `### Available skills` → `### How to use skills` |
| 目录片段 | `ext/skills/src/fragments.rs:14` | `AvailableSkillsInstructions`（role=developer） | `<skills_instructions>` 标签定义在 `protocol/src/protocol.rs:134` |
| 增量目录 | `ext/skills/src/catalog_delta.rs:384`、`world_state.rs` | 目录差异 / `## Skills update` | 目录变化时写入的增量消息 |
| 上下文写入 | `core/src/session/mod.rs:5465` | `record_context_updates_and_set_reference_context_item` | 根据是否需要全量注入，在全量写入和差异写入之间选择 |
| 意图识别 | `skills/src/mentions.rs:84` | `extract_tool_mentions` | 提取 `$name` 和路径 |
| 意图识别 | `skills/src/selection.rs:44` | **`collect_explicit_skill_mentions`** | 显式点名 → 选中的 `Vec<SkillMetadata>` |
| 正文加载 | `ext/skills/src/host_prompt.rs:70` | **`HostSkillsSnapshot::load_skill_prompts`** | 读取 SKILL.md，生成 `SkillInstructions` |
| 正文片段 | `ext/skills/src/fragments.rs:73` | `SkillInstructions`（role=user，`<skill>`） | 正文注入格式 |
| 编排 | `core/src/session/turn.rs:217, 1260, 1038` | **`run_turn`**、**`build_skills_and_plugins`**、`run_hooks_and_record_inputs` | 每个 turn 的主流程 |
| Prompt | `core/src/session/turn.rs:1842, 1891, 2888` | `build_prompt`、`run_sampling_request`、`try_run_sampling_request` | 由历史构造 Prompt 并发起采样 |
| 事件 | `core/src/skills.rs:145, 237` | `emit_explicit_skill_invocations`、`maybe_emit_implicit_skill_invocation` | 显式 / 隐式调用事件 |
| 请求 | `core/src/client.rs:2584, 1162` | `ModelClientSession::stream`、**`build_responses_request`** | 构造并发送 Responses 请求 |
| 请求体 | `codex-api/src/common.rs:287` | `ResponsesApiRequest` | 请求体结构 |

---

## 4. 上下文注入时机

1. **全量注入**
   - 在 `record_context_updates_and_set_reference_context_item` 中，当 `should_inject_full_context` 为真时，调用 `build_initial_context_with_world_state`，Skill 目录随初始上下文一起写入历史。
   - 已确认的触发场景：首个 turn 没有基线（测试 `..._injects_full_context_when_baseline_missing`）；历史被清空或压缩后（测试 `..._reinjects_full_context_after_clear`）。
2. **增量注入**
   - 其他 turn 只通过 `merge_world_state_updates` 写入差异。Skill 目录变化时，由 `catalog_delta.rs` / `world_state.rs` 生成增量消息（例如 `## Skills update`）。目录没有变化时不重复写入。
3. **正文注入**
   - 只有当前 turn 有显式点名时，才在 `build_skills_and_plugins` 中生成。
   - 在 `run_hooks_and_record_inputs` 把用户消息写入历史之后，由 `record_conversation_items`（`turn.rs:498`）写入历史。
   - 写入的是持久化的历史条目，而不是只存在于单次请求中的临时内容：同一 turn 后续的工具调用循环，以及之后的 turn，都会从 `clone_history()` 中再次带上这条正文，直到压缩替换掉历史【压缩后正文不保留，是推断】。
4. **请求时才拼接的前缀**
   - base instructions，以及使用 responses-lite 模型时的工具定义，不写入历史，而是在每次 `build_responses_request` 中重新生成，插在 `input` 最前面（见第 5 节）。

【文章】对照：
- 目录在启动时注入系统提示词。【差异】Codex 在每个 turn 开始时把目录写入历史，作为 `input` 中的 developer 消息，而且有全量和增量两种方式。
- 正文注入系统提示词，位置在“工具声明之后、对话历史之前”，格式为 `## 名称 v版本`。【差异】Codex 把正文作为 `input` 中**用户消息之后**的 user 消息，用 `<skill>` 包裹，没有版本号。
- 每 3–5 轮压缩时清理内容。【差异】源码中没有这种计数逻辑。

---

## 5. Responses API 请求的构造（`client.rs:1162` 逐行复核）

`build_responses_request` 的步骤如下：
1. `input = prompt.get_formatted_input_for_request(model_info)`：取出格式化后的历史，包含上面写入的目录、用户消息和 `<skill>` 正文。
2. 处理 `ConfigurationUpdate` 条目：模型不支持推理强度覆盖时，把这类条目过滤掉；支持时，相邻的多条只保留最新一条。
3. 构造只在请求中存在的**前缀** `prefix`：
   - 如果 `model_info.use_responses_lite` 为真，工具定义以 `ResponseItem::AdditionalTools{role:"developer"}` 的形式放进 prefix，`tools` 字段置为 `None`；否则 `tools = create_tools_raw_json_for_responses_api(&prompt.tools)`。
   - 如果 `prompt.base_instructions.text` 非空，就封装成 `BaseInstructionsFragment`（`core/src/context/base_instructions.rs`，role=`developer`，kind=`model.base_instructions`）放进 prefix。它的 ID 由 thread_id 和指令文本哈希生成，因此重试或恢复会话时保持不变。
4. `input.splice(0..0, prefix)`：把前缀插到 `input` 最前面。
5. 清理元数据：非 responses-lite 模型清除内容条目的元数据；非 OpenAI provider 还会清除加密内容等字段。
6. 组装 `ResponsesApiRequest{model, stream:true, service_tier, input, tools, tool_choice:"auto", parallel_tool_calls, reasoning, store:false, stream_options, include, prompt_cache_key, prompt_cache_options, text, client_metadata, access_programs}`。

**改正**：上一版伪请求中有 `"instructions"` 字段，这是错误的。`ResponsesApiRequest` 中**没有** `instructions` 字段，base instructions 是 `input[0]` 位置的 developer 消息。

另外，`build_prompt` 在 `incremental_tools=true` 时会把 `base_instructions` 置空，此时 `input` 中不再有这条前缀。

---

## 6. 示例：本地 Skill 走完整条链路

### 6.1 示例 Skill
`$CODEX_HOME/skills/commit-msg/SKILL.md`（默认路径为 `~/.codex/skills/…`）
```markdown
---
name: commit-msg
description: 根据 git diff 生成符合 Conventional Commits 的提交信息
---
# Commit Message
1. 运行 `git diff --staged`
2. 按 `type(scope): subject` 格式输出
```

### 6.2 链路走查
1. **加载**：`skills_load_input_from_config` 计算出 Skill 根目录；`parse_skill_frontmatter_metadata` 得到 `SkillMetadata{name:"commit-msg", description, path_to_skills_md, scope:User}`【`scope:User` 是按目录位置推断的】。
2. **turn 开始 (A)**：首个 turn 全量写入目录，其中包含 commit-msg 这一行【这一行的具体格式为推断】。
3. **意图识别 (B)**：用户输入 `用 $commit-msg 帮我写提交信息` → `extract_tool_mentions` 识别出 `$commit-msg` → `collect_explicit_skill_mentions` 命中 → `load_skill_prompts` 读取 SKILL.md 原文，生成 `SkillInstructions` → `emit_explicit_skill_invocations` 发出事件。
4. **写入历史 (C)(D)**：先写入用户消息，再写入 `<skill>` 正文。
5. **构造请求**：`build_prompt` → `stream` → `build_responses_request`，在 input 最前面插入 base instructions → `POST /responses`。

### 6.3 伪请求（按复核结果修正）
```jsonc
POST {base_url}/responses
{
  "model": "gpt-5-codex",              // 示意值
  "stream": true,
  "input": [
    // [前缀，请求时生成] BaseInstructionsFragment：role=developer，源码确认；不写入历史
    { "type": "message", "role": "developer",
      "content": [{ "type": "input_text", "text": "<Codex base instructions>" }] },

    // [历史] 初始上下文中的其他片段（例如环境上下文）。role、内容和排列顺序为【推断】
    { "type": "message", "role": "user",
      "content": [{ "type": "input_text", "text": "<environment_context>…cwd=/repo…</environment_context>" }] },

    // ★ 注入点①（历史）：AvailableSkillsInstructions，role=developer，标签与标题源码确认
    { "type": "message", "role": "developer",
      "content": [{ "type": "input_text", "text":
        "<skills_instructions>\n## Skills\nA skill is a set of instructions provided through a `SKILL.md` source. Below is the list of skills that can be used. …\n### Available skills\n- commit-msg: 根据 git diff 生成符合 Conventional Commits 的提交信息 (file: ~/.codex/skills/commit-msg/SKILL.md)\n### How to use skills\n<usage_instructions>\n</skills_instructions>" }] },
      // “- commit-msg: … (file: …)” 这一行的格式，以及使用哪一种 intro 文案，为【推断】

    // [历史] 用户本轮输入：由 run_hooks_and_record_inputs 写入，源码确认
    { "type": "message", "role": "user",
      "content": [{ "type": "input_text", "text": "用 $commit-msg 帮我写提交信息" }] },

    // ★ 注入点②（历史）：SkillInstructions，role=user，写在用户消息之后，源码确认
    // body 格式源码确认："\n<name>{name}</name>\n<path>{path}</path>{resource_access}\n{contents}\n"
    { "type": "message", "role": "user",
      "content": [{ "type": "input_text", "text":
        "<skill>\n<name>commit-msg</name>\n<path>~/.codex/skills/commit-msg/SKILL.md</path>\n---\nname: commit-msg\ndescription: 根据 git diff 生成符合 Conventional Commits 的提交信息\n---\n# Commit Message\n1. 运行 `git diff --staged`\n2. 按 `type(scope): subject` 格式输出\n</skill>" }] }
      // 本地 Skill 的 resource_access 为 None，因此没有 <resource_access> 段
  ],
  "tools": [ /* create_tools_raw_json_for_responses_api(prompt.tools)，例如 shell / apply_patch */ ],
  "tool_choice": "auto",
  "parallel_tool_calls": true,
  "reasoning": { "effort": "medium", "summary": "auto" },   // 示意值
  "store": false,
  "include": ["reasoning.encrypted_content"],             // 示意值
  "prompt_cache_key": "<thread_id 相关>",
  "client_metadata": { "...": "..." }
  // 注意：没有 "instructions" 字段
}
```

如果用户**没有**写 `$commit-msg`：
- `input` 中没有注入点②。
- 模型如果决定使用这个 Skill，会调用 shell 工具读取 SKILL.md（例如 `cat ~/.codex/skills/commit-msg/SKILL.md`）。`exec_command.rs:168` 触发 `maybe_emit_implicit_skill_invocation`，只记录调用事件和使用情况。
- 正文以 `function_call_output` 的形式出现在下一次请求的 `input` 中【输出条目的类型为推断】。

---

## 7. 文章与源码差异汇总（以源码为准）

| # | 主题 | 文章（Claude Code） | Codex 源码 |
|---|---|---|---|
| 1 | 目录位置 | 注入系统提示词 | 写入历史，作为 `input` 中的 developer 消息，用 `<skills_instructions>` 包裹 |
| 2 | 正文位置与格式 | 放在系统提示词中、对话历史之前，格式为 `## 名称 v版本` | 作为 `input` 中**用户消息之后**的 user 消息，用 `<skill><name><path>` 包裹，没有版本号 |
| 3 | 系统提示词 | 有独立的系统提示词结构 | 没有 `instructions` 字段；base instructions 作为 `input` 最前面的 developer 消息 |
| 4 | 自动匹配 | 相似度、置信度、约 0.65 阈值 | 没有这类实现；由模型根据目录自行决定 |
| 5 | 手动调用 | `/skill-name` | `$skill-name`、`skill://` 路径或结构化输入 |
| 6 | 元数据字段 | version、agent、context 等 | frontmatter 只有 name、description、short_description，另有 policy 等单独解析的元数据 |
| 7 | 正文预处理 | 改写相对链接、加载时检查依赖 | 注入原文，不做改写 |
| 8 | Level 3 资源 | 专门的资源加载器，限制 100KB / 10 个文件 | 意图链路中没有这类加载器；由模型通过工具读取 |
| 9 | 注入时机 | 启动时扫描一次 | 每个 turn 都检查：首次或压缩后全量写入，目录变化时增量写入 |
| 10 | 卸载 | 每 3–5 轮清理 | 没有这种计数逻辑 |
| 11 | 来源 | `.claude/skills` 等路径 | 按 `SkillScope::{User, Repo, System, Admin}` 区分 |

两者一致的地方：
- 都是元数据常驻、正文按需注入；
- 核心文件都是 SKILL.md；
- 正文都在模型推理之前注入；
- 同一个 Skill 只注入一次。

---

## 8. 仍为推断的内容
1. 目录中每个 Skill 那一行的具体格式（由 `render_skill_lines` 和 locator 共同决定），以及实际使用哪一种 intro 文案。
2. 初始上下文中其他片段（例如环境上下文）的 role 和排列顺序。
3. `allow_implicit_invocation` 在目录渲染或隐式识别中具体如何生效。
4. 隐式读取 SKILL.md 时，输出在下一次请求中具体是哪种条目类型。
5. 压缩历史后，已注入的正文是否会被保留。
6. 示例 Skill 的 `scope:User` 是按目录位置推断的；伪请求中的 model、reasoning、include 等取值只用于示意。

---

## 9. `collect_explicit_skill_mentions`：显式意图识别逐行分析

> 源码版本：本节基于重新克隆得到的 commit **`748ab55`**，比前文的 `c9b4267` 更新。由于是浅克隆，没法比较这两个版本中下面三个文件是否有改动；本节行号以 `748ab55` 为准。
> 涉及文件：`skills/src/selection.rs`（全文 204 行）、`skills/src/mentions.rs`（232 行）、`skills/src/name_counts.rs`（25 行），三个文件都已逐行阅读。

### 9.1 一句话结论
`collect_explicit_skill_mentions` 是一个**纯本地、同步、确定性**的函数。它做的是**标记解析加精确匹配**，判断依据只有三种：结构化输入里的路径、`$name` 文本标记、`[$name](path)` 链接里的路径。

| 问题 | 结论 | 依据 |
|---|---|---|
| 是否调用 Responses API 或任何模型 | **否** | 函数签名是 `pub fn`，不是 async（`selection.rs:44`），在三个文件中只用到 `HashMap`/`HashSet` 查找和字节扫描，没有发出网络请求或调用模型的代码 |
| 是否计算置信度或评分 | **否** | 只有布尔判断和计数，命中就选中，不命中就跳过；不存在分数、排序或阈值 |
| 是否做关键词或标记匹配 | **是**，但不是对正文做关键词搜索 | 只识别 `$` 开头的名称标记和 Markdown 链接形式的路径，名称要求**完全相等**（区分大小写）；不看 description，也不做模糊匹配或语义匹配 |

### 9.2 输入与输出
```rust
// skills/src/selection.rs:44
pub fn collect_explicit_skill_mentions(
    inputs: &[UserInput],                           // 本轮用户输入（Text / Skill 等）
    loaded_skills: &impl ExplicitSkillLookup,       // 已加载的 Skill：顺序列表、禁用路径、发现路径
    connector_slug_counts: &HashMap<String, usize>, // connector 名称计数，用于检测名称冲突
) -> Vec<SkillMetadata>                             // 被显式选中的 Skill，按确定的顺序排列
```
- `ExplicitSkillLookup`（`selection.rs:21–31`）提供以下接口：
  - `skills()`：按发现顺序排列的 Skill 列表；
  - `disabled_paths()`：已禁用 Skill 的路径；
  - `skill_discovery_path_for_path()`：某个 Skill 的逻辑发现路径；
  - `is_skill_enabled()`：默认实现是“路径不在禁用集合中”。
- 调用方是 `core/src/session/turn.rs` 中的 `build_skills_and_plugins`。

### 9.3 处理步骤

**第 0 步：统计同名数量**（`selection.rs:49–50` → `name_counts.rs:9–25`）
- `build_skill_name_counts` 跳过已禁用的 Skill，同时统计“名称原样”和“名称转小写”两种计数。
- 本函数只使用前者 `.0`，也就是**区分大小写的精确计数**，用于后面判断名称是否有歧义。
- 初始化四个集合：`selected`、`seen_names`、`seen_paths`、`blocked_plain_names`（`selection.rs:57–60`）。

**第 1 步：处理结构化 Skill 输入**（`selection.rs:62–95`）
- 只处理 `UserInput::Skill { name, path }`。
  1. **先**把 `name` 加入 `blocked_plain_names`（L64）。即使后面没能选中这个 Skill，同名的 `$name` 文本标记也不会再按名称匹配。
  2. 把 `path` 解析为相对当前工作目录的绝对路径，再转成 `PathUri`；解析失败就跳过（L65–68）。
  3. 在已加载的 Skill 中查找 `path_to_skills_md == path`，或逻辑发现路径等于 `path` 的项；找不到就跳过（L70–83）。
  4. 如果该 Skill 已被禁用，或这个路径已经选过，就跳过（L85–89）。
  5. 否则记入 `seen_paths` 和 `seen_names`，并加入 `selected`（L91–93）。
- 这一步的匹配**只看路径**，不看名称。

**第 2 步：扫描文本输入**（`selection.rs:97–109`）
- 对每条 `UserInput::Text` 调用 `extract_tool_mentions(text)`，再调用 `select_skills_from_mentions`。

**第 2a 步：从文本中提取标记**（`mentions.rs:84–149`）
- 逐字节扫描文本，识别两种形式：
  - **链接形式 `[$name](path)`**：由 `parse_linked_tool_mention` 解析（L151–206），要求依次满足：
    - `[` 后面紧跟 `$`；
    - 名称合法，并以 `]` 结束；
    - `]` 和 `(` 之间可以有空白；
    - 括号里的路径去掉首尾空白后不能为空；
    - 解析失败就退回普通扫描。
  - 链接解析成功后（L101–109）：
    - 路径放入 `paths`；
    - 名称**只放入 `names`，不放入 `plain_names`**；
    - 如果路径是 `app://`、`mcp://` 或 `plugin://` 开头，名称连 `names` 也不放入。
  - **普通形式 `$name`**（L114–141）：`$` 后面至少有一个合法字符；名称放入 `names` 和 `plain_names`。
- 名称允许的字符只有 `[A-Za-z0-9_\-:]`（`is_mention_name_char`，L226–228），遇到其他字符（包括空格、标点、中文）就结束。
  - 例如 `$commit-msg帮我` 解析出的名称是 `commit-msg`。
- 常见环境变量会被排除（`is_common_env_var`，L208–224，大小写不敏感）：PATH、HOME、USER、SHELL、PWD、TMPDIR、TEMP、TMP、LANG、TERM、XDG_CONFIG_HOME。
  - 例如 `$HOME`、`$path` 都不算 Skill 标记。
- 三个集合都是 `HashSet`，所以同一条文本里重复的标记会自动合并，并且**不保留标记在文本中出现的顺序**。

**第 2b 步：按路径匹配**（`selection.rs:133–165`）
1. 从 `paths` 中去掉 `app://`、`mcp://`、`plugin://` 开头的路径（`tool_kind_for_path`，`mentions.rs:43–55`）。
2. 用 `normalize_skill_path` 去掉 `skill://` 前缀。
3. 先按本机路径解析为 `PathUri`，失败时再按旧格式路径推断；两种都失败就丢弃（L141–146）。
4. **按已加载 Skill 的顺序**遍历：跳过已禁用和已选过的 Skill；如果某个 Skill 的 `path_to_skills_md` 或逻辑发现路径出现在上面的集合里，就选中（L149–165）。

**第 2c 步：按名称匹配**（`selection.rs:167–199`）
- 同样**按已加载 Skill 的顺序**遍历，逐项检查以下条件，任意一条不满足就跳过：
  1. Skill 已启用，并且路径没有被选过（L168–172）；
  2. 名称不在 `blocked_plain_names` 中（L174）；
  3. `plain_names` 中**精确包含**这个名称（L177）。这里区分大小写，`$Commit-Msg` 匹配不到 `commit-msg`；
  4. 启用的 Skill 中只有这一个叫这个名字（`skill_count == 1`），**并且**没有同名的 connector（L181–193）。connector 计数用名称的小写形式查找，所以和 connector 比较时**不区分大小写**；
  5. 这个名称还没被选过（`seen_names.insert` 返回 true，L195）。也就是说，同名的 Skill 如果已经通过路径选中，这里不会再选一次。
- 全部满足才选中。

### 9.4 规则汇总

**按名称匹配**
- **触发方式**：必须写成 `$name`，或者在 UI 中用结构化输入选择 Skill；不写 `$` 的“commit-msg”这类纯文本不会触发。
- **大小写**：Skill 名称区分大小写；环境变量排除、`SKILL.md` 文件名判断（`mentions.rs:57–60`）、connector 冲突检查这三处不区分大小写。
- **名称冲突**：同名 Skill 超过一个时，不能只靠 `$name` 选中，必须用路径；与 connector 同名时也只能靠路径选中。
- **名称被屏蔽**：结构化输入中出现过的名称，在文本里不再按名称匹配。

**按路径匹配**
- **触发方式**：结构化输入的 `path`，或者 `[$name](path)` 链接中的路径。
- **路径形式**：可以是 `SKILL.md` 的实际路径，也可以是逻辑发现路径；`skill://` 前缀会被去掉。
- **优先级**：路径匹配排在名称匹配之前；名称冲突对路径匹配没有影响。
- **路径找不到时**：
  - 链接形式中的名称只进入 `names`，不进入 `plain_names`，所以不会退回到按名称匹配；
  - 结构化输入的名称已经被屏蔽，同样不会退回到按名称匹配。

**去重**
- 按 `seen_paths` 去重，保证同一个 Skill 只选一次；
- 在名称匹配阶段，还会按 `seen_names` 去重。

**输出顺序**
1. 先是结构化输入命中的 Skill，按输入顺序排列；
2. 然后逐条处理文本输入，每条文本内先列出路径匹配的结果、再列出名称匹配的结果，都按**已加载 Skill 的发现顺序**排列，而不是按标记在文本中出现的顺序。

**被忽略的情况**
- Skill 已禁用；
- 名称有歧义，或与 connector 同名；
- 名称是常见环境变量；
- 路径是 app、mcp 或 plugin 类型；
- 结构化输入的路径无法解析，或者找不到对应 Skill；
- `$` 后面不是合法的名称字符；
- 名称拼写或大小写与 Skill 不一致；
- 名称已被屏蔽。

### 9.5 关键代码片段
```rust
// mentions.rs:41, 226-228 —— 标记前缀与名称允许的字符
const TOOL_MENTION_SIGIL: char = '$';
fn is_mention_name_char(byte: u8) -> bool {
    matches!(byte, b'a'..=b'z' | b'A'..=b'Z' | b'0'..=b'9' | b'_' | b'-' | b':')
}

// selection.rs:62-64 —— 结构化输入会屏蔽同名的文本标记
if let UserInput::Skill { name, path, .. } = input {
    blocked_plain_names.insert(name.clone());

// selection.rs:177-193 —— 名称匹配：精确相等、名称唯一、不与 connector 同名
if !mentions.contains_plain_name(skill.name.as_str()) { continue; }
let skill_count = selection_context.skill_name_counts.get(skill.name.as_str()).copied().unwrap_or(0);
let connector_count = selection_context.connector_slug_counts
    .get(&skill.name.to_ascii_lowercase()).copied().unwrap_or(0);
if skill_count != 1 || connector_count != 0 { continue; }
```

### 9.6 本地 Skill 输入 → 输出示例
已加载的 Skill，按发现顺序排列，均为启用状态：
- A：`commit-msg`，路径 `/home/u/.codex/skills/commit-msg/SKILL.md`
- B：`review`，路径 `/home/u/.codex/skills/review/SKILL.md`
- C：`review`，路径 `/repo/.codex/skills/review/SKILL.md`（与 B 同名）
- D：`deploy`，路径 `/home/u/.codex/skills/deploy/SKILL.md`

connector 计数中有 `deploy`：1。

| # | 用户输入（Text） | 解析出的标记 | 输出 | 原因 |
|---|---|---|---|---|
| 1 | `用 $commit-msg帮我写提交信息` | plain_names={commit-msg} | **[A]** | 名称在启用的 Skill 中唯一，也没有同名 connector |
| 2 | `用 $Commit-Msg 写提交信息` | plain_names={Commit-Msg} | [] | 名称区分大小写，与 A 不一致 |
| 3 | `帮我写提交信息` | 无 | [] | 没有 `$` 标记；Codex 不做语义匹配，是否用 Skill 交给模型决定 |
| 4 | `$review 这段代码` | plain_names={review} | [] | 启用的 Skill 中有两个 `review`，名称有歧义 |
| 5 | `[$review](/repo/.codex/skills/review/SKILL.md) 这段代码` | paths={…/repo/…}，names={review} | **[C]** | 按路径精确匹配 |
| 6 | `$deploy 一下` | plain_names={deploy} | [] | 与 connector `deploy` 同名 |
| 7 | `$HOME 下的 $commit-msg 和 $commit-msg` | plain_names={commit-msg} | **[A]** | `$HOME` 被当作环境变量排除；同名标记只保留一次 |
| 8 | `[$commit-msg](/missing/SKILL.md)` | paths={/missing/…}，plain_names 为空 | [] | 路径找不到对应 Skill，链接中的名称也不会退回到按名称匹配 |
| 9 | `$deploy 然后 $commit-msg`，同时有结构化输入 `Skill{name:"review", path:"/home/u/.codex/skills/review/SKILL.md"}` | — | **[B, A]** | 结构化输入先选中 B；文本中 `$deploy` 因与 connector 同名被跳过，`$commit-msg` 选中 A |

第 9 例中，如果把 `$deploy` 换成 `$review`：B 已经由结构化输入选中，`review` 这个名称也已被屏蔽，所以不会重复选中，也不会改选 C。

### 9.7 与【文章】对照
- 【文章】4.1 节：对 description 做语义相似度计算，按 0.0–1.0 打置信度分，阈值约 0.65。
- 【差异】Codex 的显式识别中**完全没有**这一套：不读取 description，不调用模型，也不打分。没有显式点名时，是否使用 Skill 完全交给模型，依据是目录中的 description（见第 1 节的隐式路径）。

### 9.8 本节中的推断
- `ExplicitSkillLookup` 在 Codex 中的具体实现类型，以及逻辑发现路径具体由哪里计算，本次没有追踪。
- `connector_slug_counts` 在调用方的构造方式，本次没有逐行核对。
- 示例中各 Skill 的路径和 connector 名称都是虚构的，只用于演示匹配规则。
