# DeepSeek 原生 Responses API 开发规则

> 文档核对日期：2026-11-02。“当前官方行为”来自核对时的 DeepSeek 文档，模型和能力变化时应重新核对。
> 有冲突时以官方文档和可复现的真实 API 行为为准。

## 0. 本项目适配

- **落点**：DeepSeek 线上格式只写在 `backend/core/src/providers.py`，必须保持可替换；`tools.py` 不知道任何模型 API 格式，`agent.py` 只依赖 provider 的统一接口。
- **范围**：只做最小闭环。不实现审批策略、权限限制、工具沙箱及 MCP（见 `rule/harness.md`）。
- **依赖**：运行时依赖只有 `openai`、`rich`、`prompt_toolkit`，未经询问不得新增。
- **验证**：先写测试并看它失败，再实现；测试用 `FakeProvider`、`FakeOpenAI`、`tmp_path`，绝不调用真实 API。真实 API 检查写成 `@pytest.mark.live` 测试，不写一次性脚本。
- **不要运行 agent 本身**来验证改动，它会调用付费 API 并等待交互输入。
- **既有约定仍然成立**：每个 `call_id` 恰好一条结果，失败也要回传错误；工具失败不能让循环崩溃；修剪只替换旧工具结果的内容、不删消息，且批量进行以保护前缀缓存。

## 1. 官方来源

- [Responses API 使用指南](https://api-docs.deepseek.com/guides/responses_api/)
- [创建 Response：请求和响应定义](https://api-docs.deepseek.com/api/create-response)
- [思考模式与历史回传](https://api-docs.deepseek.com/guides/thinking_mode)
- [工具调用说明](https://api-docs.deepseek.com/guides/tool_calls)（其中 Chat Completions 示例不可直接复制为 Responses 格式）
- [错误码](https://api-docs.deepseek.com/quick_start/error_codes)
- [上下文缓存](https://api-docs.deepseek.com/guides/kv_cache)

## 2. 接口与鉴权

- 直接调用 `POST https://api.deepseek.com/responses`，请求头 `Authorization: Bearer <DEEPSEEK_API_KEY>`。
- 密钥从环境变量 `DEEPSEEK_API_KEY` 读取，不写入源码、版本库或日志。
- 使用 OpenAI Python SDK 时设置 `base_url="https://api.deepseek.com"`，调用 `client.responses.create(...)`。
- 不得误用 `/chat/completions`，也不通过 Responses → Chat Completions 代理实现。
- 模型 ID 当前为 `deepseek-flash` 或 `deepseek-v4-pro`，可配置，不依赖历史别名。

最小请求：

```json
{
  "model": "deepseek-flash",
  "instructions": "You are a helpful assistant.",
  "input": "请用一句话介绍 Responses API。",
  "reasoning": {"effort": "none"},
  "max_output_tokens": 1024,
  "stream": false
}
```

## 3. 参数规则

| 需求 | 正确字段与约束 |
| --- | --- |
| 用户输入 | `input`，字符串或 input item 数组；`input` 与 `instructions` 至少提供一个 |
| 系统指令 | `instructions`；无状态调用，每次都要发送 |
| 推理强度 | `reasoning: {"effort": "none/low/high/max"}`；`none` 关闭思考；省略时默认开启 |
| 输出预算 | `max_output_tokens`，包含可见文本与推理 token；思考模式要预留足够 |
| 函数工具 | `tools` 中使用扁平的 `type/name/description/parameters` |
| 工具选择 | `tool_choice` 为 `none/auto/required` 或 `{"type":"function","name":"..."}` |
| 流式输出 | `stream: true`，使用 Responses 语义 SSE 事件 |

必须遵守：

1. 不使用 Chat Completions 的 `messages`、顶层 `reasoning_effort`、`max_tokens`、`response_format` 或嵌套 `tools[].function`。
2. 思考模式下 `temperature` 不生效；默认省略采样参数。
3. 不依赖 `previous_response_id`、`conversation`、`store`、`background`、`include`、`metadata`、`truncation`、`stream_options`（当前未支持，可能被静默忽略，HTTP 200 不证明有效）。
4. `parallel_tool_calls`、`max_tool_calls` 当前被忽略：并行工具调用始终开启，客户端必须能接收多个调用；循环步数、工具数量和耗时由客户端限制。

## 4. 会话与历史

- API 无状态，客户端维护历史，每次通过 `input` 发送完整且有效的历史。
- 保存模型输出 item 的顺序和函数调用的 `call_id/name/arguments`，其后追加对应的 `function_call_output`。
- 思考开启且带工具时，必须回传服务实际生成的推理内容：用 `reasoning` item 与 `reasoning_text` content part，不能把 Chat 的 `reasoning_content` 字段塞进请求。
- 不伪造缺失的推理内容；未返回 reasoning item 就保留其余实际输出。
- 输入端不支持 reasoning 的 `summary` 和 `encrypted_content`，序列化时移除，保留明文 content。
- 不直接把整个 response 对象追加到 input；只转换已支持的 output items，未知类型明确报错。
- `developer` 角色会按 `user` 处理；系统级约束用 `instructions`。
- 超出上下文窗口返回 400；压缩历史时不能拆散调用与结果的配对。

工具定义与结果示例：

```json
{"type":"function","name":"get_utc_date","description":"Return the current UTC date as YYYY-MM-DD.",
 "parameters":{"type":"object","properties":{},"additionalProperties":false}}
```

```json
{"type":"function_call_output","call_id":"<模型返回的 call_id>","output":"{\"date\":\"2026-11-02\"}"}
```

## 5. 最小工具循环

1. 用户消息加入历史，以历史构造请求。
2. 等 response 完整返回，检查 HTTP 状态与 `response.status`。
3. 仅 `completed` 且调用完整有效时才进入工具执行。
4. 把支持的 output items 按原顺序转换并加入历史。
5. 无工具调用则返回文本；有调用则逐个验证工具名、JSON 参数和 `call_id`。
6. 执行工具，每个调用追加一条与其 `call_id` 配对的结果，再请求模型。
7. 达到循环或预算上限时明确停止，不报告为任务完成。

模型参数不是可信代码：禁止 `eval` 和直接拼接 Shell，工具走显式注册表。参数错误或工具失败回传错误结果，让模型修正。同一个 `call_id` 不得重复执行。

## 6. SSE 与输出解析

- SSE 解析要处理跨 chunk 的 UTF-8 字符、分片事件、空行分隔和多行 data，不能把每个网络 chunk 当作完整 JSON。
- 处理 `response.output_text.delta`、`response.reasoning_text.delta`、`response.function_call_arguments.delta/done`、`response.output_item.added/done`。
- 多个 item 的增量按 item 身份与 output 索引分别聚合，不混拼不同调用的 arguments。
- 结束事件为 `response.completed`、`response.incomplete`、`response.failed`，没有 `data: [DONE]`。
- 不执行流式片段、截断 JSON 或 incomplete 响应中的调用。无终态即断流视为不完整，保留已显示文本并报告中断。
- 答案从 `output` 中 message 的 `output_text` 提取，不假设 `output[0]` 是答案。
- `usage` 从完整响应或流式终态读取；区分“缺失”和“0”。

## 7. 错误与超时

- 设置超时并允许用户取消。
- 400 先查 schema、历史配对和上下文长度；401/402 明确报告，不盲目重试。
- 429 和临时 5xx 用有上限的指数退避；不要叠加 SDK 与业务层重试。
- `incomplete` 保留部分输出与 `incomplete_details`，`failed` 保留 error，二者都不能当 completed。
- 日志只记录模型、耗时、状态、用量和脱敏错误码，不记录 Authorization、完整请求或推理明文。

## 8. 验收

用 fake 写成 `uv run pytest` 可跑的测试，至少覆盖：

- 普通文本、两轮会话（第二轮确实带上第一轮历史）。
- 关闭/开启思考；带工具时保留真实 reasoning items。
- 一次函数调用、同轮多个调用、未知工具、非法 JSON、重复 `call_id`。
- completed/incomplete/failed；无终态断流和参数分片不触发工具执行。
- 400/401/429/5xx 与超时；重试达到上限后明确退出。
- 循环达到上限时停止。

修改 `providers.py` 后运行 live 测试（会调用真实 API 并产生费用，密钥在 `.env`）：

```bash
uv run --env-file .env pytest -m live
```

提交前：

```bash
uvx ruff check backend/core/src backend/core/tests && uvx ruff format --check backend/core/src backend/core/tests
```

验证状态要分开记录：本地 fake、静态检查、真实 API。只有完成真实请求才能标记“实测可运行”。
