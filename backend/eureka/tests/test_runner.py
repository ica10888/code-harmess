"""runner 测试：队列、常驻线程、阶段推进、错误处理、超时、重启恢复。

不依赖 Django：runner 通过一个 Store 接口读写状态，这里用内存假存储；线程用 run_once() 同步驱动，测试不用 sleep。
对应参考仓库 LoopSchedule：FIFO 队列 + 一个常驻消费者，出错的任务被记录、循环不崩。
"""

import threading
import time

import pytest

from eureka import registry
from eureka.base import EurekaPlugin, Stage, StageResult
from eureka.runner import Runner


class MemoryStore:
    """内存里的存储：runner 只用这些方法读写状态。"""

    def __init__(self):
        self.runs = {}  # run_id -> {"status", "error", "plugin", "payload", "session_id", "workspace"}
        self.stages = {}  # run_id -> [{"key","title","method","status","output","error"}]
        self.logs = {}  # (run_id, key) -> [text]
        self.events = []  # 顺序记录：("run", id, status) / ("stage", id, key, status)

    def add_run(self, run_id, plugin, stages, payload=None, session_id="s", workspace=None):
        self.runs[run_id] = dict(status="pending", error="", plugin=plugin, payload=payload or [], session_id=session_id, workspace=workspace)
        self.stages[run_id] = [dict(key=s.key, title=s.title, method=s.method, status="pending", output={}, error="") for s in stages]

    # --- runner 用到的接口 ---
    def load_run(self, run_id):
        run = self.runs.get(run_id)
        return None if run is None else {**run, "id": run_id, "stages": [dict(s) for s in self.stages[run_id]]}

    def set_run_status(self, run_id, status, error=""):
        self.runs[run_id]["status"], self.runs[run_id]["error"] = status, error
        self.events.append(("run", run_id, status))

    def set_stage(self, run_id, key, status, output=None, error=""):
        stage = next(s for s in self.stages[run_id] if s["key"] == key)
        stage["status"], stage["error"] = status, error
        if output is not None:
            stage["output"] = output
        self.events.append(("stage", run_id, key, status))

    def add_log(self, run_id, key, text):
        self.logs.setdefault((run_id, key), []).append(text)

    def unfinished_run_ids(self):
        return [i for i, r in self.runs.items() if r["status"] in ("pending", "running")]


class Recorder(EurekaPlugin):
    """按脚本执行的假插件：每个阶段记录自己被调用，可以让某个阶段抛异常 / 卡住。"""

    name = "demo"

    def __init__(self, fail_at=None, sleep_at=None, outputs=None):
        super().__init__()
        self.calls = []
        self.fail_at, self.sleep_at, self.outputs = fail_at, sleep_at, outputs or {}

    def stages(self):
        return [Stage(k, k.upper(), f"reconcile_{k}") for k in ("one", "two", "three")]

    def _do(self, key, ctx):
        self.calls.append((key, dict(ctx.previous)))
        ctx.log(f"开始 {key}")
        if self.sleep_at == key:
            deadline = time.time() + 5
            while not ctx.cancelled and time.time() < deadline:
                time.sleep(0.01)
        if self.fail_at == key:
            raise RuntimeError(f"{key} 出错了")
        return StageResult(output=self.outputs.get(key, {"from": key}))

    def reconcile_one(self, ctx):
        return self._do("one", ctx)

    def reconcile_two(self, ctx):
        return self._do("two", ctx)

    def reconcile_three(self, ctx):
        return self._do("three", ctx)


def build(plugin=None, **runner_kw):
    store = MemoryStore()
    plugin = plugin or Recorder()
    reg = registry.Registry()
    reg._plugins[plugin.name] = plugin
    runner = Runner(store, reg, **runner_kw)
    return runner, store, plugin


def submit(runner, store, plugin, run_id=1, **kw):
    store.add_run(run_id, plugin.name, plugin.stages(), **kw)
    runner.enqueue(run_id)


# --- 阶段推进 ---


def test_stages_run_in_order_and_the_run_ends_done():
    runner, store, plugin = build()
    submit(runner, store, plugin)

    runner.run_once()

    assert [c[0] for c in plugin.calls] == ["one", "two", "three"]
    assert store.runs[1]["status"] == "done"
    assert [s["status"] for s in store.stages[1]] == ["done"] * 3


def test_each_stage_is_marked_running_then_done_before_the_next_one_starts():
    runner, store, plugin = build()
    submit(runner, store, plugin)

    runner.run_once()

    assert store.events == [
        ("run", 1, "running"),
        ("stage", 1, "one", "running"), ("stage", 1, "one", "done"),
        ("stage", 1, "two", "running"), ("stage", 1, "two", "done"),
        ("stage", 1, "three", "running"), ("stage", 1, "three", "done"),
        ("run", 1, "done"),
    ]  # fmt: skip


def test_a_stage_output_is_saved_and_handed_to_the_following_stages():
    runner, store, plugin = build(Recorder(outputs={"one": {"n": 5}, "two": {"m": 6}}))
    submit(runner, store, plugin)

    runner.run_once()

    assert store.stages[1][0]["output"] == {"n": 5}
    assert plugin.calls[1][1] == {"one": {"n": 5}}
    assert plugin.calls[2][1] == {"one": {"n": 5}, "two": {"m": 6}}


def test_log_lines_printed_through_the_webhook_are_saved_under_their_stage():
    runner, store, plugin = build()
    submit(runner, store, plugin)

    runner.run_once()

    assert store.logs[(1, "one")] == ["开始 one"] and store.logs[(1, "three")] == ["开始 three"]


def test_the_context_carries_the_runs_payload_session_and_workspace(tmp_path):
    seen = {}

    class Spy(Recorder):
        def reconcile_one(self, ctx):
            seen.update(run_id=ctx.run_id, session_id=ctx.session_id, payload=ctx.payload, workspace=ctx.workspace)
            return StageResult()

    runner, store, plugin = build(Spy())
    submit(runner, store, plugin, run_id=7, payload=[{"a": 1}], session_id="sess-9", workspace=tmp_path)

    runner.run_once()

    assert seen == dict(run_id=7, session_id="sess-9", payload=[{"a": 1}], workspace=tmp_path)


# --- 失败 ---


def test_a_failing_stage_marks_itself_and_the_run_failed_and_later_stages_stay_pending():
    runner, store, plugin = build(Recorder(fail_at="two"))
    submit(runner, store, plugin)

    runner.run_once()

    assert [s["status"] for s in store.stages[1]] == ["done", "failed", "pending"]
    assert store.runs[1]["status"] == "failed" and "two 出错了" in store.runs[1]["error"]
    assert "two 出错了" in store.stages[1][1]["error"]
    assert [c[0] for c in plugin.calls] == ["one", "two"]  # 第三阶段没有被调用


def test_a_failure_does_not_stop_the_consumer_the_next_run_still_goes():
    runner, store, plugin = build(Recorder(fail_at="one"))
    submit(runner, store, plugin, run_id=1)
    runner.run_once()

    plugin.fail_at = None
    submit(runner, store, plugin, run_id=2)
    runner.run_once()

    assert store.runs[1]["status"] == "failed" and store.runs[2]["status"] == "done"  # 对应参考仓库的 recover()


def test_the_error_text_shown_to_the_user_is_only_the_message_not_a_traceback():
    runner, store, plugin = build(Recorder(fail_at="one"))
    submit(runner, store, plugin)

    runner.run_once()

    error = store.stages[1][0]["error"]
    assert "Traceback" not in error and "File \"" not in error


def test_an_exception_that_mentions_a_secret_is_not_echoed_if_it_matches_a_known_secret():
    secret = "sk-very-secret-key-123456"

    class Leaky(Recorder):
        def reconcile_one(self, ctx):
            raise RuntimeError(f"请求失败，key={secret}")

    runner, store, plugin = build(Leaky(), redact=[secret])
    submit(runner, store, plugin)

    runner.run_once()

    assert secret not in store.stages[1][0]["error"] and secret not in store.runs[1]["error"]


def test_a_plugin_returning_the_wrong_type_is_a_failure_not_a_crash():
    class Wrong(Recorder):
        def reconcile_one(self, ctx):
            return "not a StageResult"

    runner, store, plugin = build(Wrong())
    submit(runner, store, plugin)

    runner.run_once()

    assert store.stages[1][0]["status"] == "failed" and "StageResult" in store.stages[1][0]["error"]


def test_a_non_json_output_is_a_failure_because_it_could_not_be_saved():
    class Bad(Recorder):
        def reconcile_one(self, ctx):
            return StageResult(output={"x": object()})

    runner, store, plugin = build(Bad())
    submit(runner, store, plugin)

    runner.run_once()

    assert store.stages[1][0]["status"] == "failed"


def test_an_unknown_plugin_fails_the_run_instead_of_crashing_the_loop():
    runner, store, plugin = build()
    store.add_run(1, "ghost", plugin.stages())
    runner.enqueue(1)

    runner.run_once()

    assert store.runs[1]["status"] == "failed" and "ghost" in store.runs[1]["error"]


def test_a_missing_run_is_skipped():
    runner, store, _ = build()
    runner.enqueue(404)
    runner.run_once()  # 不抛


def test_a_run_that_is_already_done_is_not_run_again():
    runner, store, plugin = build()
    submit(runner, store, plugin)
    runner.run_once()
    plugin.calls.clear()

    runner.enqueue(1)
    runner.run_once()

    assert plugin.calls == []  # 幂等：同一个运行不会被重复执行


# --- 队列：FIFO 串行 ---


def test_runs_are_executed_one_at_a_time_in_the_order_they_were_enqueued():
    runner, store, plugin = build()
    for i in (1, 2, 3):
        submit(runner, store, plugin, run_id=i)

    for _ in range(3):
        runner.run_once()

    order = [e[1] for e in store.events if e[0] == "run" and e[2] == "running"]
    assert order == [1, 2, 3]


def test_run_once_returns_false_when_the_queue_is_empty():
    runner, _, _ = build()
    assert runner.run_once(block=False) is False


def test_enqueueing_the_same_run_twice_while_it_waits_only_queues_it_once():
    runner, store, plugin = build()
    submit(runner, store, plugin)
    runner.enqueue(1)
    runner.enqueue(1)

    assert runner.pending_count() == 1


# --- 超时 ---


def test_a_stage_that_takes_too_long_is_cancelled_and_marked_failed():
    runner, store, plugin = build(Recorder(sleep_at="one"), stage_timeout=0.2)
    submit(runner, store, plugin)

    started = time.time()
    runner.run_once()

    assert time.time() - started < 3  # 没有等满 5 秒
    assert store.stages[1][0]["status"] == "failed" and "超时" in store.stages[1][0]["error"]
    assert store.runs[1]["status"] == "failed"


def test_after_a_timeout_the_next_run_is_not_blocked():
    runner, store, plugin = build(Recorder(sleep_at="one"), stage_timeout=0.2)
    submit(runner, store, plugin, run_id=1)
    runner.run_once()
    plugin.sleep_at = None
    submit(runner, store, plugin, run_id=2)

    runner.run_once()

    assert store.runs[2]["status"] == "done"


# --- 重启恢复 ---


def test_recover_requeues_runs_that_were_pending_or_running_when_the_process_stopped():
    old_runner, store, plugin = build()
    store.add_run(1, plugin.name, plugin.stages())
    store.runs[1]["status"] = "running"  # 上次跑到一半进程退出
    store.add_run(2, plugin.name, plugin.stages())  # 还没来得及入队
    store.add_run(3, plugin.name, plugin.stages())
    store.runs[3]["status"] = "done"

    new_runner = Runner(store, old_runner._registry)  # 进程重启：一个全新的 runner，队列是空的
    count = new_runner.recover()

    assert count == 2 and new_runner.pending_count() == 2


def test_a_recovered_run_resumes_from_the_first_stage_that_is_not_done():
    runner, store, plugin = build()
    store.add_run(1, plugin.name, plugin.stages())
    store.runs[1]["status"] = "running"
    store.stages[1][0].update(status="done", output={"from": "one"})
    store.stages[1][1]["status"] = "running"  # 中断时正在跑这个

    runner.recover()
    runner.run_once()

    assert [c[0] for c in plugin.calls] == ["two", "three"]  # 第一阶段已完成，不重做
    assert plugin.calls[0][1] == {"one": {"from": "one"}}  # 但它的输出仍然交给后面
    assert store.runs[1]["status"] == "done"


def test_recover_does_not_touch_finished_or_failed_runs():
    runner, store, plugin = build()
    for i, status in ((1, "done"), (2, "failed")):
        store.add_run(i, plugin.name, plugin.stages())
        store.runs[i]["status"] = status

    assert runner.recover() == 0


# --- 常驻线程 ---


def test_the_background_thread_processes_the_queue_and_can_be_stopped():
    runner, store, plugin = build()
    runner.start()
    try:
        submit(runner, store, plugin)
        deadline = time.time() + 5
        while store.runs[1]["status"] != "done" and time.time() < deadline:
            time.sleep(0.01)
        assert store.runs[1]["status"] == "done"
    finally:
        runner.stop()
    assert not runner.is_alive()


def test_start_is_idempotent_only_one_consumer_thread_exists():
    runner, _, _ = build()
    runner.start()
    runner.start()
    try:
        names = [t.name for t in threading.enumerate() if t.name == "eureka-runner"]
        assert len(names) == 1
    finally:
        runner.stop()


def test_an_exception_inside_the_loop_itself_does_not_kill_the_thread():
    runner, store, plugin = build()

    class ExplodingStore(MemoryStore):
        def load_run(self, run_id):
            if run_id == 1:
                raise RuntimeError("db down")
            return super().load_run(run_id)

    store2 = ExplodingStore()
    runner = Runner(store2, runner._registry)
    runner.start()
    try:
        runner.enqueue(1)  # 加载就抛
        store2.add_run(2, plugin.name, plugin.stages())
        runner.enqueue(2)
        deadline = time.time() + 5
        while store2.runs[2]["status"] != "done" and time.time() < deadline:
            time.sleep(0.01)
        assert store2.runs[2]["status"] == "done"  # 线程活着，后面的运行照常
    finally:
        runner.stop()


# --- ctx.log 走注入的 webhook 工厂（不是直接写库）---------------------------------------


def test_the_stage_log_goes_through_the_injected_webhook_factory_not_straight_to_the_store():
    made = []

    def factory(run_id, key):
        lines = []
        made.append((run_id, key, lines))
        return lines.append

    runner, store, plugin = build(webhook_factory=factory)
    submit(runner, store, plugin)

    runner.run_once()

    assert [(r, k) for r, k, _ in made] == [(1, "one"), (1, "two"), (1, "three")]  # 每个阶段一个 webhook
    assert made[0][2] == ["开始 one"]
    assert store.logs == {}  # 没有绕过 webhook 直接写库


def test_without_a_factory_the_runner_falls_back_to_writing_through_the_store():
    runner, store, plugin = build()
    submit(runner, store, plugin)
    runner.run_once()
    assert store.logs[(1, "one")] == ["开始 one"]


def test_a_factory_that_raises_does_not_fail_the_stage_it_just_loses_the_logs():
    def broken(run_id, key):
        raise RuntimeError("cannot build webhook")

    runner, store, plugin = build(webhook_factory=broken)
    submit(runner, store, plugin)

    runner.run_once()

    assert store.runs[1]["status"] == "done"  # 打印不了日志，但阶段照常完成
