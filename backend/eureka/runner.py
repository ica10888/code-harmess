"""runner：队列 + 常驻后台线程 + 阶段推进。对应参考仓库 multi-tenancy-operator 的 LoopSchedule：
一个 FIFO 队列，一个常驻消费者按先进先出串行执行；每个任务的异常被记录，不让整个循环崩（它的 recover()）。

和参考仓库的 Reconcile 一样，触发方只「决定要做什么」就把任务放进队列返回（enqueue），不在自己的线程里长时间执行，
所以聊天回复不会被工作流阻塞。

runner 不依赖 Django：状态的读写都经过一个 Store 对象（见下面的 Store 协议），测试里用内存假存储。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .base import StageContext, StageResult, validate_stages
from .registry import Registry

logger = logging.getLogger(__name__)

DEFAULT_STAGE_TIMEOUT = 60.0  # 秒
MAX_ERROR_CHARS = 1_000


class Store(Protocol):
    """runner 读写状态要用的方法。Django 版的实现在 apps 里，测试用内存版。"""

    def load_run(self, run_id: int) -> dict | None:
        """{"id","plugin","status","payload","session_id","workspace","stages":[{key,title,method,status,output,error}]}"""

    def set_run_status(self, run_id: int, status: str, error: str = "") -> None: ...

    def set_stage(self, run_id: int, key: str, status: str, output: dict | None = None, error: str = "") -> None: ...

    def add_log(self, run_id: int, key: str, text: str) -> None: ...

    def unfinished_run_ids(self) -> list[int]: ...


class Runner:
    def __init__(
        self,
        store: Store,
        registry: Registry,
        stage_timeout: float = DEFAULT_STAGE_TIMEOUT,
        redact: list[str] | None = None,
        webhook_factory: Callable[[int, str], Callable[[str], None]] | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._timeout = stage_timeout
        # 给每个阶段造一个 webhook（真实 HTTP + 降级写库，见 webhook.py）；不给就直接经 store 写
        self._webhook_factory = webhook_factory
        self._redact = [s for s in (redact or []) if s]  # 已知的密钥：就算异常信息里带着，也不展示给用户
        self._queue: queue.Queue[int] = queue.Queue()
        self._waiting: set[int] = set()  # 已经在队列里等着的 run_id：同一个运行不重复入队
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    # --- 入队（触发方调用，立刻返回）-----------------------------------------------

    def enqueue(self, run_id: int) -> None:
        with self._lock:
            if run_id in self._waiting:
                return
            self._waiting.add(run_id)
        self._queue.put(run_id)

    def pending_count(self) -> int:
        return self._queue.qsize()

    def recover(self) -> int:
        """进程重启后把没做完的运行（pending / running）重新入队，返回入队个数。阶段幂等，所以可以重做。"""
        ids = self._store.unfinished_run_ids()
        for run_id in ids:
            self.enqueue(run_id)
        return len(ids)

    # --- 消费 ----------------------------------------------------------------------

    def run_once(self, block: bool = True) -> bool:
        """取一个运行并执行完。队列空（非阻塞时）返回 False。出任何错都只记录，不往外抛。"""
        try:
            run_id = self._queue.get(block=block, timeout=0.2 if block else None)
        except queue.Empty:
            return False
        with self._lock:
            self._waiting.discard(run_id)
        try:
            self._execute(run_id)
        except Exception:  # noqa: BLE001  对应参考仓库的 recover()：一个任务出错不影响后面的
            logger.exception("eureka runner：执行运行 %s 时出错", run_id)
        return True

    def _execute(self, run_id: int) -> None:
        run = self._store.load_run(run_id)
        if run is None:
            logger.warning("eureka runner：运行 %s 不存在，已跳过", run_id)
            return
        if run["status"] in ("done", "failed"):
            return  # 幂等：已经结束的运行不再执行
        plugin = self._registry.get(run["plugin"])
        if plugin is None:
            self._store.set_run_status(run_id, "failed", f"找不到插件 {run['plugin']}（config.ini 的 [eureka] plugins 里没有启用它）")
            return

        self._store.set_run_status(run_id, "running")
        previous = {s["key"]: s["output"] for s in run["stages"] if s["status"] == "done"}  # 恢复时已完成的阶段不重做
        by_key = {s.key: s for s in validate_stages(plugin)}

        for stage in run["stages"]:
            if stage["status"] == "done":
                continue
            ok = self._run_stage(run, plugin, by_key.get(stage["key"]), stage, previous)
            if not ok:
                return  # 失败：后面的阶段保持 pending，整个运行已被标记为 failed
        self._store.set_run_status(run_id, "done")

    def _run_stage(self, run: dict, plugin, declared, stage: dict, previous: dict) -> bool:
        run_id, key = run["id"], stage["key"]
        self._store.set_stage(run_id, key, "running")
        ctx = StageContext(
            run_id=run_id,
            session_id=run["session_id"],
            payload=run["payload"],
            workspace=Path(run["workspace"]) if run["workspace"] else None,
            log=self._log_for(run_id, key),
            previous=dict(previous),
        )
        outcome: dict = {}

        def work() -> None:
            try:
                if declared is None:
                    raise RuntimeError(f"插件 {plugin.name} 的阶段声明里已经没有 {key}")
                result = getattr(plugin, declared.method)(ctx)
                if not isinstance(result, StageResult):
                    raise TypeError(f"阶段 {key} 必须返回 StageResult，拿到的是 {type(result).__name__}")
                json.dumps(result.output)  # 要存进数据库：不能保存的输出当场报错，而不是等写库时才炸
                outcome["result"] = result
            except Exception as e:  # noqa: BLE001  插件代码可能抛任何异常
                logger.exception("eureka 插件 %s 的阶段 %s 出错", plugin.name, key)
                outcome["error"] = e

        # 阶段放在单独的线程里跑，runner 只等它 timeout 秒：Python 不能安全地杀线程，
        # 所以超时只是「标记失败并放弃等待」，同时置 ctx.cancelled，让守规矩的插件尽快退出。
        worker = threading.Thread(target=work, name=f"eureka-stage-{run_id}-{key}", daemon=True)
        worker.start()
        worker.join(self._timeout)
        if worker.is_alive():
            ctx.cancel()
            self._fail(run_id, key, f"阶段 {stage['title']} 超时（超过 {self._timeout:g} 秒），已放弃等待")
            return False
        if "error" in outcome:
            self._fail(run_id, key, str(outcome["error"]) or type(outcome["error"]).__name__)
            return False

        result = outcome["result"]
        previous[key] = result.output
        self._store.set_stage(run_id, key, "done", output=result.output)
        return True

    def _log_for(self, run_id: int, key: str) -> Callable[[str], None]:
        """这个阶段的 ctx.log：走注入的 webhook；造不出来（或没注入）就直接经 store 写。造 webhook 出错不能让阶段失败。"""
        if self._webhook_factory is not None:
            try:
                return self._webhook_factory(run_id, key)
            except Exception:  # noqa: BLE001
                logger.exception("eureka runner：给运行 %s 的阶段 %s 创建 webhook 失败，这个阶段的日志会丢失", run_id, key)
                return lambda text: None
        return lambda text: self._store.add_log(run_id, key, text)

    def _fail(self, run_id: int, key: str, message: str) -> None:
        message = self._clean(message)
        self._store.set_stage(run_id, key, "failed", error=message)
        self._store.set_run_status(run_id, "failed", message)

    def _clean(self, message: str) -> str:
        """给用户看的错误信息：只留异常消息本身（不含 traceback），抹掉已知的密钥，并限制长度。"""
        for secret in self._redact:
            message = message.replace(secret, "***")
        return message[:MAX_ERROR_CHARS]

    # --- 常驻线程 --------------------------------------------------------------------

    def start(self) -> None:
        """启动常驻消费线程；已经在跑就什么都不做。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._loop, name="eureka-runner", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                self.run_once(block=True)
            except Exception:  # noqa: BLE001  循环本身永远不退出
                logger.exception("eureka runner 循环出错，继续")
                time.sleep(0.1)

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
