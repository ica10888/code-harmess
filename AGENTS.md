# 项目说明

一个 Harness 的最小实践
包含一个前后架构，前端 Vue 3 + Element Plus，后端 Django 4.2 
core 实现一个最小 Harness ，然后通过后端方法调用

清晰优先于功能：改动要小，要易读。


## 目录结构

```
code-harness/
├── backend/          # Django 后端（端口 8000）
│   ├── config.ini    # 数据库及服务器配置（勿提交密码）
│   ├── manage.py
│   ├── core/    # 最小 Harness 实现
│   ├── code_backend/ # Django 项目配置
│   └── apps/         # 业务模块（models / views / serializers / urls）
└── frontend/         # Vue 3 前端（端口 5173）
    └── src/
        ├── api/      # axios 封装
        ├── router/   # vue-router
        └── views/    # 前端页面展示
```


## 环境与依赖

- Python 依赖用 `uv` 装进项目根目录的 `.venv`；后端依赖写在 `backend/requirements.txt`
- Node.js 24，npm 11

## 常用命令

在项目根目录执行：

```bash
# 安装后端依赖（首次）
uv pip install -r backend/requirements.txt

# 后端启动
uv run python backend/manage.py runserver

# 数据库迁移
uv run python backend/manage.py makemigrations apps
uv run python backend/manage.py migrate

# 后端测试（Django 的测试，不在根目录 pytest 的范围内）
uv run python backend/manage.py test apps

# 模拟后端主动给前端发消息
uv run python backend/manage.py push_message <会话id> "文字"

# 前端安装依赖（首次）和启动（需在独立终端运行）
cd frontend && npm install
cd frontend && npm run dev
```

### 代理设置

由于网络环境限制，需要配置代理才能访问 GitHub：

```bash
# 配置 HTTP 代理（推荐）
git config --global http.proxy http://127.0.0.1:7890

# 取消代理配置
git config --global --unset http.proxy
git config --global --unset https.proxy
```


### 提交代码流程

```bash
# 1. 查看状态
git status

# 2. 添加文件
git add <文件路径>

# 3. 提交
git commit -m "提交信息

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"

# 4. 推送到远程
git push
```

## 注意事项

- 前端 vite 已配置 `/api` 代理到 `http://127.0.0.1:8000`，后端必须先启动
- 不要运行 `npm run dev` 等长期进程作为后台任务，告知用户手动执行
- 修改 models 后必须重新执行 `makemigrations` + `migrate`





## 命令

- 后端启动命令 
```cd C:\Users\Admin\Documents\code-harmness
$env:Path = "C:\Users\Admin\.local\bin;$env:Path"
uv run python backend/manage.py runserver
```
- 前端启动命令
```
cd C:\Users\Admin\Documents\code-harmness\frontend
npm run dev
```
- 测试：`uv run pytest`（很快，绝不调用真实 API；CI 跑的就是这个）
- Live 测试：`uv run --env-file .env pytest -m live` —— 调用真实的 DeepSeek API，要花钱，默认跳过。
  修改 `providers.py`、`prompt.py`、工具描述或上下文修剪之后，或被要求时，运行它们。
- Lint / 格式检查：`uvx ruff check backend/core/src backend/core/tests && uvx ruff format --check backend/core/src backend/core/tests`
- 课程检查，修改 `backend/core/src/` 或 `.agents/personas/` 之后：`uv run rh-learn-python/tools/check_lessons.py`、
  `uv run rh-learn-agent-harness/tools/check_lessons.py`、`uv run rh-learn-agent-harness/tools/build_lesson01.py --check`。
  `rh-learn-agent-harness/lessons/01-three-agents.html` 是生成的：编辑 `tools/templates/01-three-agents.html`，然后重新构建。
- 自动修复格式：`uvx ruff format backend/core/src backend/core/tests`
- 所有 Python 相关的事都用 `uv`；绝不 `pip install`，也绝不用系统的 `python3` 运行脚本。
  独立脚本（例如 skill 的 `scripts/`）用 PEP 723 的 `# /// script` 块内联声明依赖（没有依赖就写
  `dependencies = []`），并用 `uv run <script>` 运行：这样 uv 会给它们一个隔离的环境，而不是借用本项目的
  `.venv`。运行时依赖是 `openai`、`rich`（渲染 Markdown）和 `prompt_toolkit`（多行输入编辑）——
  未经询问不得新增。
- 不要为了验证改动而运行 agent 本身（`backend/core/src/agent.py`、`backend/core/src/step1_chat.py`）：它会调用付费的
  DeepSeek API，并且会等待交互式输入。请用上面的测试来验证。

## 架构

- `backend/core/src/tools.py` —— 工具（`Tool` = name / description / input_schema / run）。对任何模型 API 一无所知。
- `backend/core/src/providers.py` —— 唯一知道 DeepSeek 线上格式（OpenAI 兼容）的地方。必须保持可替换。
- `backend/core/src/prompt.py` —— `build_system_prompt(PromptContext)` 是纯函数；I/O（git、AGENTS.md、记忆索引）放在
  由 `main()` 调用的单独函数里。
- `backend/core/src/skills.py` —— 发现 skill（Agent Skills 标准：`<dir>/<name>/SKILL.md`），不用 PyYAML 解析 frontmatter，
  构建 `# Skills` 索引。项目 skill 放在 `.agents/skills/`（与具体 agent 无关的约定；
  不是 `.claude/skills`）。共享的 skill 会提交；个人的通过 `.git/info/exclude` 排除。
  在 SKILL.md 里，用相对于 skill 文件夹的路径引用 skill 自己的文件（`scripts/x.py`）。
- `backend/core/src/personas.py` —— 发现 persona（`.agents/personas/<id>.md`：frontmatter + 正文）。正文替换系统提示词里
  默认的角色那一行；`tools:` 从 `ALL_TOOLS` 中选择。`agent.py` 把每个 persona 变成一个
  `Session`，各有自己的 conversation、approver 和记忆目录（`<memory root>/<id>/`）；provider 是共享的。
- `backend/core/src/agent.py` —— 主循环。依赖上面这些模块（以及 `cli_input.py`）；没有任何模块依赖它。
- `backend/core/src/` 在 pytest 路径上，所以测试里 import 的是 `agent`、`tools`……（不是 `backend.core.src.agent`）。

## 约定

- 代码注释、docstring、README 和 commit message 用中文；标识符、工具描述和
  发给模型的文本用英文。
- 例外：persona 文件（`.agents/personas/*.md`）是用户内容，和 `AGENTS.md`、`SKILL.md` 一样，
  用中文书写，方便年轻的学习者阅读和编辑。harness 内置的提示词段落和工具
  描述仍然用英文。`Tool.summary_zh` 是用于展示（`/tools`）的中文一句话，绝不会发给模型。
- 测试使用 fake（`FakeProvider`、`FakeOpenAI`、`tmp_path`），并通过公开接口测试行为。
- 先写测试，看它失败，再实现。
- 针对真实 API 的检查写进 `tests/test_live.py`，作为 `@pytest.mark.live` 测试，断言行为
  （调用了哪些工具、回答里的关键事实）——绝不写一次性脚本。只有纯粹的
  探索（例如测量 token 曲线）可以用一次性脚本；任何应当一直成立的东西
  都要变成 live 测试。

## 经验教训

- 模型发来的每个 `tool_call` id 都必须得到恰好一条 `role=tool` 回复，即使调用失败——
  绝不能丢掉任何一个调用，否则下一次 API 请求会被拒绝。
- DeepSeek 带工具的思考模式：`reasoning_content` 必须在后续请求中回传
  （见 `DeepSeekProvider._assistant_message`）。
- 工具失败要作为错误结果返回给模型；绝不能让循环崩溃。
- 绝不提交 `.env`（里面是 API key）；`.env.example` 是模板。
- DeepSeek 会自动缓存请求前缀。任何改动靠前消息的做法（每轮编辑系统
  提示词、时间戳、每一步都修剪旧消息）都会让缓存从那一点起失效。
  这就是修剪要批量进行的原因：只在输入超过 `CONTEXT_BUDGET` 时，一次性全部修剪。
- 修剪是把旧工具结果的*内容*换成占位符；绝不从 `conversation` 里删除消息。
- `CONTEXT_BUDGET` 必须远大于所保留的 `KEEP_TOOL_RESULTS` 条结果的大小，否则修剪
  每一步都会触发。保留的结果太少，模型就会重新读它仍然需要的文件。
- 记忆笔记是 `Memory/`（`MINI_HARNESS_MEMORY_DIR` 的默认值）里的普通 Markdown 文件。该文件夹被
  gitignore，只留在本地。绝不提交记忆笔记：之后再删除并不能把它们从 git 历史里清除。
  除非项目确实需要，否则不要添加第三方记忆工具（例如 Basic Memory）。
  笔记存放知识，而不是代码：可复用的脚本属于 skill（`.agents/skills/<name>/scripts/`），
  笔记只需指向它。
- `docs/*.pdf` 是参考资料——不要编辑。
