"""eureka 工作流在 Django 里的运行时：把 backend/eureka（不依赖 Django）接到数据库、配置和触发入口上。

  - DjangoStore：runner 读写状态要用的那组方法，落到 WorkflowRun / StageRun / StageLog 上；
  - get_runner / get_registry：进程内唯一的注册表和 runner（延迟创建，测试里 reset() 清掉）；
  - start_run：eureka 命中后创建运行记录并入队（触发方不等它执行完，对应参考仓库 Reconcile 的「只决定、入队」）；
  - recover：进程重启后把没做完的运行重新入队；
  - write_log_directly：webhook 的降级路径，和接收接口用同一套去重规则。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from eureka import registry as eureka_registry
from eureka.base import MAX_LOG_CHARS, validate_stages
from eureka.runner import Runner
from eureka.webhook import Webhook

from . import harness
from .models import Eureka, StageLog, StageRun, WorkflowRun
from .workflows_run import run_token

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_registry: eureka_registry.Registry | None = None
_runner: Runner | None = None


def write_log_directly(run_id: int, stage_key: str, seq: int, text: str) -> bool:
    """直接写一行日志。(stage, seq) 已经有了就什么都不做（重发安全，先写的赢）；返回是否真的新写入。

    每个阶段最多保留 EUREKA_MAX_LOG_LINES 行：服务端也限制，不能只靠发送端自觉。
    """
    stage = StageRun.objects.filter(run_id=run_id, key=stage_key).first()
    if stage is None:
        return False
    if stage.logs.filter(seq=seq).exists() or stage.logs.count() >= settings.EUREKA_MAX_LOG_LINES:
        return False
    try:
        with transaction.atomic():
            StageLog.objects.create(stage=stage, seq=seq, text=text[:MAX_LOG_CHARS])
    except IntegrityError:  # 并发下两个请求同时写同一个 seq：后到的被唯一约束挡住
        return False
    return True


class DjangoStore:
    """runner 的 Store：把状态落到数据库。"""

    def load_run(self, run_id: int) -> dict | None:
        run = WorkflowRun.objects.filter(pk=run_id).select_related("eureka__message__session").first()
        if run is None:
            return None
        session = run.eureka.message.session
        return {
            "id": run.id,
            "plugin": run.plugin,
            "status": run.status,
            "payload": run.payload,
            "session_id": str(session.id),
            "workspace": harness.workspace_dir(session),
            "stages": [
                {"key": s.key, "title": s.title, "method": s.method, "status": s.status, "output": s.output, "error": s.error}
                for s in run.stages.all()
            ],
        }

    def set_run_status(self, run_id: int, status: str, error: str = "") -> None:
        fields = {"status": status, "error": error}
        if status in ("done", "failed"):
            fields["finished_at"] = timezone.now()
        WorkflowRun.objects.filter(pk=run_id).update(**fields)

    def set_stage(self, run_id: int, key: str, status: str, output: dict | None = None, error: str = "") -> None:
        fields: dict = {"status": status, "error": error}
        now = timezone.now()
        if status == "running":
            fields["started_at"] = now
        if status in ("done", "failed"):
            fields["finished_at"] = now
        if output is not None:
            fields["output"] = output
        # 状态、输出、结束时间在同一次 UPDATE 里写：「完成」和它的输出不会只写一半
        StageRun.objects.filter(run_id=run_id, key=key).update(**fields)

    def add_log(self, run_id: int, key: str, text: str) -> None:
        stage = StageRun.objects.filter(run_id=run_id, key=key).first()
        if stage is not None:
            write_log_directly(run_id, key, stage.next_log_seq(), text)

    def unfinished_run_ids(self) -> list[int]:
        return list(WorkflowRun.objects.filter(status__in=["pending", "running"]).order_by("id").values_list("id", flat=True))


def _load_registry() -> eureka_registry.Registry:
    """按 config.ini 加载插件，并把需要的配置注入给它们。插件不读配置、不 import Django，所以由这里注入。"""
    loaded = eureka_registry.load(eureka_registry.parse_plugin_list(settings.EUREKA_PLUGINS))
    for warning in loaded.warnings:
        logger.warning("eureka 插件：%s", warning)
    code_review = loaded.get("code-review")
    if code_review is not None:
        code_review.chain_factory = _langchain_factory  # 它的「生成修改」阶段要调模型
    deploy_game = loaded.get("frontend-game-deploy")
    if deploy_game is not None:
        deploy_game.deploy_root = settings.EUREKA_DEPLOY_DIR
        deploy_game.public_host = settings.EUREKA_DEPLOY_HOST
        deploy_game.start_timeout = settings.EUREKA_DEPLOY_START_TIMEOUT
    return loaded


def _langchain_factory():
    """造 code_review 用的 LangChain 链。每次用到才造（读当时的配置），所以加载插件时不碰 LangChain、不联网。"""
    from eureka.plugins.code_review.plugin import langchain_chain

    llm = settings.EUREKA_LLM
    if not llm["api_key"] or llm["api_key"].startswith("sk-your"):
        raise ValueError("没有配置 DeepSeek 的 key：请在 backend/config.ini 的 [eureka] 里填 llm_api_key（或 [deepseek] api_key）")
    return langchain_chain(
        api_key=llm["api_key"],
        model=llm["model"],
        base_url=llm["base_url"],
        reasoning_effort=llm["reasoning_effort"],
        timeout=llm["timeout"],
    )


def get_registry() -> eureka_registry.Registry:
    global _registry
    with _lock:
        if _registry is None:
            _registry = _load_registry()
        return _registry


def get_runner() -> Runner:
    global _runner
    with _lock:
        if _runner is None:
            _runner = Runner(
                DjangoStore(),
                get_registry_unlocked(),
                stage_timeout=settings.EUREKA_STAGE_TIMEOUT,
                redact=_secrets(),
                webhook_factory=make_webhook,
            )
        return _runner


def get_registry_unlocked() -> eureka_registry.Registry:
    # get_runner 已经持有锁时用：不能再 with _lock（threading.Lock 不可重入）
    global _registry
    if _registry is None:
        _registry = _load_registry()
    return _registry


def _secrets() -> list[str]:
    """错误信息里绝不能出现的密钥：DeepSeek 的 key（聊天用的和 eureka 插件用的）和 Django 的 SECRET_KEY。"""
    return [settings.DEEPSEEK_API_KEY, settings.EUREKA_LLM["api_key"], settings.SECRET_KEY]


def reset() -> None:
    """清掉进程内的注册表和 runner（停掉后台线程）。测试用；配置改了想重新加载时也用。"""
    global _registry, _runner
    with _lock:
        if _runner is not None:
            _runner.stop(timeout=1)
        _registry = _runner = None


def start() -> None:
    """启动后台消费线程，并把上次没做完的运行重新入队。只在真正的服务进程里调用（见 apps.py）。"""
    runner = get_runner()
    runner.start()
    count = runner.recover()
    if count:
        logger.info("eureka：重启后恢复 %s 个未完成的工作流运行", count)


def recover() -> int:
    return get_runner().recover()


def make_webhook(run_id: int, stage_key: str) -> Webhook:
    """给插件用的 webhook：真实 HTTP，不通就降级直接写库。"""
    return Webhook(
        settings.EUREKA_WEBHOOK_BASE_URL,
        run_id,
        stage_key,
        run_token(run_id),
        fallback=write_log_directly,
    )


def start_run(eureka: Eureka, payload) -> WorkflowRun | None:
    """eureka 命中后展开工作流：找插件 → 在一个事务里创建运行和各阶段 → 提交之后才入队。

    找不到插件（没在 config.ini 里启用）就什么都不做，返回 None：eureka 提示照常显示，只是不展开工作流。
    同一条 eureka 只展开一次（WorkflowRun 一对一挂在 Eureka 上）。
    """
    if hasattr(eureka, "run"):
        return eureka.run
    plugin = get_registry().get(eureka.plugin)
    if plugin is None:
        return None
    stages = validate_stages(plugin)
    with transaction.atomic():
        run = WorkflowRun.objects.create(eureka=eureka, plugin=plugin.name, payload=payload)
        StageRun.objects.bulk_create(
            [StageRun(run=run, index=i, key=s.key, title=s.title, method=s.method) for i, s in enumerate(stages)]
        )
        transaction.on_commit(lambda: get_runner().enqueue(run.id))  # 提交之后才入队：队列先于数据可见会读不到
    return run
