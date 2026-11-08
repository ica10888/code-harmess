# 项目说明

@rule/deepseek-responses.md

一个 Harness 的最小实践
包含一个前后架构，前端 Vue 3 + Element Plus，后端 Django 4.2 ，SQLite
core 实现一个最小 Harness ，然后通过后端方法调用

清晰优先于功能：改动要小，要易读。


## 目录结构

```
code-harness/
├── rule/             # 项目规则
│   ├── harness.md             # 最小 Harness 实现规则（由 commands 按需引用）
│   ├── deepseek-responses.md  # DeepSeek Responses API 开发规则
│   └── commands/     # 斜杠命令源文件；.claude/commands 是指向它的 junction
├── backend/          # Django 后端（端口 8000）
│   ├── config.ini    # 数据库及服务器配置（勿提交密码）
│   ├── manage.py
│   ├── core/    # 最小 Harness 实现
│   ├── eureka/    # eureka 插件实现
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
- WebFetch 使用代理 http://127.0.0.1:7890
- 前端动效组件按需从 Inspira UI（基于 shadcn-vue、motion-v、TailwindCSS 的 Vue 组件库）复制引入，不整体安装；查文档先读 https://inspira-ui.com/llms.txt，再按其中链接取 `raw/en/components/<分类>/<名称>.md`（需走代理 127.0.0.1:7890，WebFetch 被拦时用 curl）。





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
  `.venv`。运行时依赖是 `openai`、`rich`（渲染 Markdown）和 `prompt_toolkit`（多行输入编辑）和
  `jsonschema`（后端工作流按 schema 匹配模型回复）——未经询问不得新增。
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

## eureka 插件机制（设计方案）

> 状态：只是方案，还没有代码。评审通过后再按下面「实施步骤」逐步做，仍然先写测试、看它失败、再实现。
> 参考：<https://github.com/ica10888/multi-tenancy-operator>（Go 写的 Kubernetes operator，核对的版本 `63aeb55`，2020-07-29）。

### 1. 目标

用户用了某个插件（skill），模型的回复通过了同名工作流的 schema，就触发 eureka 时刻（已实现：`Eureka` 表和提示条）。
这个方案要做的是：**eureka 触发之后，展开一个多阶段的工作流**，每个阶段由一个「eureka 插件」实现。

1. 插件实现统一的基本方法：声明这个工作流有几个阶段，返回给主项目。
2. 每个阶段对应插件的一个 `reconcile_<阶段>` 方法，里面写业务逻辑；执行中通过 webhook 把「打印文字」发回主项目，
   主项目把文字存下来推给前端；阶段做完，把这个阶段标记为完成。
3. 主项目只认插件接口，不认具体插件；换一个插件，主项目代码不变。

### 2. 参考仓库里值得借鉴的设计

参考仓库做的是「改了 CRD 的 spec，operator 把集群调到那个状态」。它的结构和我们要的几乎一一对应：

| 参考仓库（Go） | 作用 | 本项目（Python）对应 |
| --- | --- | --- |
| `Reconcile(request)`（`multi_tenancy_controller.go`） | 状态变了就回调，**只负责「算出要做什么」然后入队**，不直接干活 | 触发入口：eureka 命中后生成一个「运行记录」并入队 |
| `TenancyQueue`（FIFO channel）+ `LoopSchedule` 里常驻的一个 goroutine | 一个消费者按先进先出**串行**执行，用 `Mutex` 保护共享状态 | 一个常驻的后台线程 + `queue.Queue`，串行执行运行记录 |
| `TenancyScheduler` / `TenancyWatcher` 接口（`interface.go`） | 主程序只认接口 | `EurekaPlugin` 抽象基类（见下） |
| `init.go`：`LoopSchedule(tenancyscheduler.ChartSchedulerFor(), tenancywatcher.ReplicationControllerWatcherFor())`，注释写着「like Plugins」 | **编译期选实现**，不是动态加载 | 注册表：启动时按 `config.ini` 的列表导入插件，按名称取 |
| `recoverScheduleProcessor`：`defer recover()`，出错就把错误写进 status，**不让整个循环崩** | 一个任务出错不影响后面的任务 | 每个阶段 `try/except`，错误写进阶段记录，工作流标记失败，线程继续取下一个 |
| `CREATE / UPDATE / DELETE / INIT` 四种操作，`switch` 分发 | 一个队列项带「做什么」 | 一个运行记录带「从哪个阶段开始」 |
| `ControllerStatus` / `ChartMessage.ErrorMessage` | **状态写回自定义资源的 status**，前端 / 用户看 status 就知道进度和错误 | 状态写回数据库的 `WorkflowRun` / `StageRun`，前端读它 |
| `equalTenancies` 比较 spec 和 `localSpec`，没变化就不重复入队 | **幂等**：同一个期望状态不重复执行 | 同一条 eureka 只展开一次（`Eureka` 一对一 `WorkflowRun`） |
| `UpdateSingleTenancyByConfigure` 里「新旧模板差异」`removeListIfNotChanged` | 只重做变化的部分 | 失败后「从失败的那个阶段继续」，已完成的阶段不重做 |

**要特别学的三点**
- Reconcile 只「决定」，不「长时间执行」：先把任务放进队列就返回，避免阻塞触发方（对我们来说，就是不能阻塞聊天回复）。
- 状态是数据，写回存储：这样刷新页面、重启服务后，仍然知道做到哪一步。
- 出错要被记录而不是被吞掉，也不能把消费者循环带崩。

**不照搬的点**
- 参考仓库的「插件」是编译期选构造函数，没有真正的动态加载；我们同样不做动态加载（不 `import` 用户上传的代码，这会让任意上传变成任意代码执行）。
- 它用全局可变变量（`localSpec`、`inited`、全局 `Mutex`）；我们用数据库里的记录和队列代替，不用全局状态。
- 它依赖 Kubernetes 的 list & watch；我们没有这个机制，进度推送用已有的 SSE。

### 3. 总体架构

```
 聊天回复 ──命中 eureka──▶ services.save_assistant_message
                               │  1. 存 Eureka（已有）
                               │  2. 找到同名插件 → 创建 WorkflowRun + 各阶段 StageRun（状态 pending）
                               │  3. 放进队列，立刻返回（不阻塞聊天）
                               ▼
                    ┌────────────────────────┐
                    │ backend/eureka/runner  │  一个常驻后台线程，FIFO 串行
                    │  取出 WorkflowRun       │
                    │  for 阶段 in 插件.stages│
                    │     标记 running        │
                    │     插件.reconcile_x(ctx)│──▶ ctx.log("…") ──▶ webhook ──▶ 主项目存 StageLog
                    │     标记 done / failed  │
                    └────────────────────────┘
                               │
              前端 SSE ◀───────┘  拉取 WorkflowRun / StageRun / StageLog 的进度
```

目录：

```
backend/eureka/                 # eureka 插件实现（独立于 apps，和 core 一样不依赖 Django 视图）
├── base.py                     # EurekaPlugin 抽象基类、Stage、StageContext、StageResult（插件接口，主项目只认这里）
├── registry.py                 # 注册表：按 config.ini 的列表加载插件，按名称取
├── runner.py                   # 队列 + 常驻后台线程 + 阶段推进（对应参考仓库的 LoopSchedule）
├── webhook.py                  # 把「打印文字」发回主项目（真实 HTTP，见 §6）
└── plugins/
    └── code_review/            # 示例插件：和默认工作流「code-review」同名
        └── plugin.py
backend/apps/                   # 主项目（Django）
├── models.py                   # + WorkflowRun / StageRun / StageLog
├── views.py                    # + 运行进度接口、webhook 接收接口
└── services.py                 # eureka 命中后创建并入队
```

依赖方向（单向，和现有 `core` 一致）：`apps` → `eureka.registry/runner` → `eureka.base`；插件 → `eureka.base`。
`eureka` 不 import `apps`（要写库，经 runner 注入的回调，见 §5），这样插件和 Django 解耦、能单独测试。

### 4. 插件接口（主项目只认这一个）

```python
class EurekaPlugin(ABC):
    name: str            # 插件名，等于工作流 frontmatter 的 name（如 "code-review"）；注册表按它查找

    @abstractmethod
    def stages(self) -> list[Stage]:
        """基本方法①：声明这个工作流有几个阶段，返回给主项目。顺序就是执行顺序。"""

    # 基本方法②：每个阶段一个 reconcile_<阶段名> 方法，由 Stage.method 指向，不在基类里写死
```

```python
@dataclass(frozen=True)
class Stage:
    key: str             # 阶段标识，如 "extract"；同一个插件内唯一，小写字母/数字/下划线
    title: str           # 给人看的名字，如 "提取审查结果"
    method: str          # 插件上实现这个阶段的方法名，如 "reconcile_extract"

class StageContext:      # 传给每个 reconcile 的上下文（插件只能通过它和主项目打交道）
    run_id: int          # 这次工作流运行的 id
    session_id: str      # 触发它的会话
    payload: dict        # 触发 eureka 的那份 JSON（已经通过 schema 校验）
    workspace: Path      # 这个会话的工作区目录（只读写这里）
    previous: dict       # 前面已完成阶段的输出：{"extract": {...}}
    def log(self, text: str) -> None: ...   # webhook：打印一行文字，主项目存下并推给前端

@dataclass
class StageResult:
    output: dict = field(default_factory=dict)   # 本阶段的输出，给后面阶段用（存库）
```

**`reconcile_<阶段>(ctx) -> StageResult` 的约定**（借鉴 Kubernetes 的 Reconcile 语义）
- **幂等**：同一阶段重复执行结果一样、不产生副作用叠加（写文件用覆盖而不是追加）。这样失败重跑、服务重启后接着跑都安全。
- **成功返回 `StageResult`**；**失败抛异常**，由 runner 捕获并记录（插件不用自己 try/except 整个方法）。
- 过程中随时 `ctx.log("…")` 打印进度文字；不要自己写数据库。
- 不允许长时间无输出：每个阶段有超时（§8）。

### 5. 阶段状态机和数据模型

阶段状态：`pending → running → done`，或 `running → failed`；工作流整体状态：`pending → running → done / failed`。

```
WorkflowRun                         # 一次 eureka 触发的一次工作流运行（一对一挂在 Eureka 上）
  eureka      OneToOne → Eureka     # 同一条 eureka 只展开一次（幂等）
  plugin      CharField             # 插件名
  status      pending/running/done/failed
  payload     JSONField             # 触发的那份 JSON
  error       TextField(blank)      # 失败原因
  created_at / finished_at

StageRun                            # 一个阶段的运行记录，随 WorkflowRun 一起创建（全部 pending）
  run         FK → WorkflowRun
  index       int                   # 顺序，唯一 (run, index)
  key / title / method              # 照抄 Stage，保存当时的快照（以后插件改了阶段，老记录不变）
  status      pending/running/done/failed
  output      JSONField             # StageResult.output
  error       TextField(blank)
  started_at / finished_at

StageLog                            # webhook 收到的每一行文字
  stage       FK → StageRun
  seq         int                   # 本阶段内的顺序，唯一 (stage, seq)
  text        TextField
  created_at
```

「完成后更新该阶段完成」就是 runner 在 `reconcile_x` 正常返回后，**在同一个数据库事务里**写 `StageRun.status=done`、`output`、`finished_at`，
然后才推进到下一个阶段；失败则写 `failed` 和 `error`，工作流置 `failed`，**后面的阶段保持 pending**。

**与上下文的关系**：这些表和 `Eureka` 一样，**不进模型上下文**（上下文只在 `ChatSession.conversation`）。

### 6. webhook 的设计（你选的是「真实的 HTTP webhook」）

插件调用 `ctx.log(text)` → `eureka/webhook.py` 向主项目发一个 HTTP `POST`：

```
POST /api/eureka/runs/<run_id>/stages/<stage_key>/logs/
Authorization: Bearer <该次运行的一次性令牌>
{"seq": 3, "text": "已提取 5 条审查建议"}
```

主项目的接收接口把它存成 `StageLog`，前端通过 SSE 看到。

这里有几个**必须处理**的问题，否则真实 HTTP 会引入新的风险：

1. **鉴权**：这个接口不能对外裸奔，否则任何人能往别人的运行里塞文字。每次运行创建时生成一个随机令牌（只存哈希），
   只通过 `StageContext` 交给插件；接口校验令牌、并且令牌只对这一个 `run_id` 有效。
2. **只允许本机**：接口额外校验来源是回环地址（`127.0.0.1`），部署成多机时再放开。
3. **同进程时的死锁风险**：插件线程在 Django 进程内，向**自己**所在的服务发 HTTP。Django 开发服务器默认多线程可以处理，
   但若用单线程服务器，请求会等自己而死锁。方案：webhook 请求设短超时（2 秒），失败时**降级为直接写库**并记一条警告，
   这样即使 HTTP 不通，阶段文字也不丢。
4. **重试与顺序**：每条带 `seq`；接收端按 `(stage, seq)` 去重，所以重发是安全的。
5. **失败不影响阶段本身**：webhook 发送失败只丢日志（或降级），不能让阶段失败。

> 这是方案里风险最高的一项。如果评审后觉得对「最小 harness」过重，可以退回「进程内回调函数」：
> `ctx.log` 直接调 runner 注入的函数写库，接口和插件代码**完全不变**，只换 `webhook.py` 的实现。

### 7. 触发与执行流程

1. 聊天回复命中 eureka（现有逻辑：`save_assistant_message`）。
2. 新增：用 `hit["plugin"]` 去注册表找插件；找不到 → 只显示 eureka 提示，不展开工作流（现有行为不变）。
3. 找到 → 在**同一个事务**里创建 `WorkflowRun`（pending）和全部 `StageRun`（pending，来自 `plugin.stages()`），然后 `runner.enqueue(run_id)`。
   事务提交之后才入队，避免队列先于数据可见。
4. `runner` 的后台线程按 FIFO 取出，逐阶段执行（§5），每个阶段：置 `running` → 调 `reconcile_x(ctx)` → 置 `done/failed`。
5. 前端在聊天里那条 eureka 提示下面，用现有 SSE 模式订阅 `GET /api/eureka/runs/<run_id>/stream/`，显示「阶段 1/3 完成、日志……」。
6. 一次只跑一个工作流（FIFO 串行），后来的排队，状态显示 `pending`（对应参考仓库用全局 `Mutex` 保护共享状态的做法）。

**重启恢复**：进程重启时，把状态为 `running` 的运行**重新入队**（该阶段幂等，所以可以重做）；`pending` 的也入队。启动时在 `AppConfig.ready()` 里做，并且只在真正的服务进程里做（避开 `migrate`、`test`、`runserver` 的自动重载子进程）。

### 8. 错误、超时与限制

- 每个阶段 `try/except Exception`：记录 `error`，工作流 `failed`，**线程不退出**，继续取下一个运行（对应参考仓库 `recover()`）。
- 阶段超时（`config.ini` 可配，默认 60 秒）：Python 不能安全地杀线程，所以超时只是**标记失败并放弃等待**，插件应在自己的循环里检查 `ctx.cancelled`。
- 一个会话同时最多 1 个运行；一个会话最多保留最近 N 个运行记录（防止无限增长）。
- 日志单条最长 2000 字符、单阶段最多 500 条，超出截断并提示。
- 插件抛出的异常信息会展示给用户，**不得包含密钥**：runner 只存 `str(e)`，不存 traceback 到用户可见字段（完整 traceback 只写服务端日志）。

### 9. 配置（`config.ini`）

```ini
[eureka]
text = ...                     ; 已有：提示条文本
plugins = code_review          ; 新增：要加载的插件（对应 backend/eureka/plugins/<名字>/），逗号分隔；留空表示不展开任何工作流
stage_timeout_seconds = 60     ; 新增：单个阶段的超时
webhook_base_url = http://127.0.0.1:8000   ; 新增：插件回调主项目用的地址
```

### 10. 前端

- 聊天里 eureka 提示条（已有）下方，新增「工作流进度」折叠面板：阶段列表（待办 / 进行中 / 完成 / 失败的图标和名称）+ 每个阶段的日志文字。
- 进度通过 SSE 订阅；断线重连用 `Last-Event-ID`，和现有 `iter_events` 的做法一致。
- 刷新页面后，从 `GET /api/sessions/<id>/messages/` 带回的 `eureka.run` 里取最新状态，没跑完的继续订阅。

### 11. 测试策略（先写测试）

- `backend/eureka/` 的测试**不依赖 Django**：用一个假的 `StageContext` 测插件的每个阶段；runner 用内存里的假存储测状态推进。
- 必测的行为：阶段按顺序执行；前一个失败后面不执行；失败不影响下一个运行；幂等（同一阶段重跑结果一样）；
  重启后 `running` 的会重新入队；同一条 eureka 不会展开两次；webhook 失败时降级写库、阶段不失败；
  令牌错误 / 别的 run 的令牌被拒（403）；日志 `seq` 去重；超时被标记；异常信息不泄露密钥。
- 用 `FakeProvider` 之类的假对象，**不调用真实 API**；联调时再用真实后端走一遍。

### 12. 实施步骤（评审通过后，每步都是「先写测试 → 失败 → 实现」）

1. `backend/eureka/base.py`：`EurekaPlugin`、`Stage`、`StageContext`、`StageResult` + 对应测试。
2. `backend/eureka/registry.py` + 配置读取 + 测试。
3. 模型 `WorkflowRun / StageRun / StageLog` 和迁移 + 测试。
4. `runner.py`（队列、线程、阶段推进、错误处理、重启恢复）+ 测试（用内存假存储）。
5. 示例插件 `plugins/code_review`（阶段：提取建议 → 汇总 → 写出报告文件）+ 测试。
6. `webhook.py` 和接收接口（令牌、回环限制、去重、降级写库）+ 测试。
7. 接进 `save_assistant_message`：命中 eureka 且找到插件 → 创建运行并入队。
8. 进度接口和 SSE + 序列化器带上 `eureka.run`。
9. 前端进度面板。
10. 联调（真实后端 + 真实 DeepSeek）：勾选插件 → 回复触发 eureka → 工作流逐阶段执行 → 刷新后进度仍在。

### 13. 已确认的决定

- 示例插件放在 `backend/eureka/plugins/code_review/`，和默认工作流 `code-review` 同名。
- webhook 用真实 HTTP；HTTP 失败时降级为直接写库（阶段文字不丢）。
- 示例插件的三个阶段：提取建议 → 汇总 → 写出报告文件。
- 全局一个后台线程串行执行，多个会话同时触发时排队。
- 进程重启后，未完成的运行（`pending` / `running`）重新入队；阶段幂等，所以可以重做。

实现时对 §6 的一处细化：令牌不存库，由 `SECRET_KEY` 对 `run_id` 做 HMAC 派生，接收端重新计算并用常量时间比较；
这样重启恢复后不需要重新分发令牌。

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
