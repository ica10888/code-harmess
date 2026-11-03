"""eureka 工作流的 Django 集成：存储、触发、接收 webhook、进度接口。

插件和 runner 本身的行为在 backend/eureka/tests 里测；这里测的是它们和 Django（数据库、接口、触发入口）的接合。
runner 用 run_once() 同步驱动，测试不开后台线程。
"""

import contextlib
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps import harness, workflow_runtime, workflows
from apps.models import ChatSession, Eureka, Message, StageLog, StageRun, WorkflowRun
from apps.test_eureka import PLUGIN, RecordingProvider
from apps.test_harness import text
from apps.workflows_run import run_token

ORIGINAL_MODELS = "import hashlib\nclass User:\n    password = None\n"
FIXED_MODELS = "class User:\n    password = None  # write_only\n"

SUGGESTIONS = json.dumps(
    [
        {"file": "models.py", "startLineNum": 3, "endLineNum": 4, "sugDesc": "问题 A", "sugCode": "x = 1", "codeLanguage": "python", "type": "QUESTION"},
        {"file": "models.py", "startLineNum": 9, "endLineNum": 9, "sugDesc": "建议 B", "sugCode": "", "codeLanguage": "python", "type": "SUGGESTION"},
    ],
    ensure_ascii=False,
)


class BaseMixin:
    """共用的准备工作。注意 captureOnCommitCallbacks 在 TransactionTestCase 里也能用。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.skills, self.flows = self.tmp / "skills", self.tmp / "flows"
        self.skills.mkdir()
        self.flows.mkdir()
        (self.skills / "代码审查.md").write_bytes(PLUGIN.format(name="code-review").encode("utf-8"))
        # 用项目里真实的默认工作流（带完整的 code-review schema），测的就是真实路径
        real = Path(__file__).resolve().parent.parent / "default_workflow" / "代码审查.md"
        (self.flows / "代码审查.md").write_bytes(real.read_bytes())
        override = override_settings(
            HARNESS_STORAGE_DIR=str(self.tmp / "s"),
            HARNESS_DEFAULT_SKILLS_DIR=str(self.skills),
            HARNESS_DEFAULT_WORKFLOW_DIR=str(self.flows),
            EUREKA_TEXT="🎉 Eureka！",
            EUREKA_PLUGINS="code_review",
            EUREKA_WEBHOOK_BASE_URL="http://127.0.0.1:8000",
            EUREKA_STAGE_TIMEOUT=10,
        )
        override.enable()
        self.addCleanup(override.disable)
        workflow_runtime.reset()  # 每个测试用全新的注册表和 runner（不开后台线程）
        self.model_calls = []
        self.model_reply = FIXED_MODELS
        self._patch_langchain()
        self.addCleanup(workflow_runtime.reset)
        self.client = APIClient()
        self.sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        from urllib.parse import quote

        self.client.patch(f"/api/sessions/{self.sid}/plugins/{quote('代码审查.md')}/", {"enabled": True}, format="json")
        self.put_original()  # 默认：用户上传过被审查的 models.py（要在会话创建之后：工作区是按会话 id 分的）

    def _patch_langchain(self):
        """集成测试里绝不调真实模型：把 LangChain 的链换成替身，记下它收到了什么。"""

        def fake_chain(**_config):
            def call(inputs):
                self.model_calls.append(inputs)
                return self.model_reply

            return call

        patcher = mock.patch("eureka.plugins.code_review.plugin.langchain_chain", side_effect=fake_chain)
        patcher.start()
        self.addCleanup(patcher.stop)

    def put_original(self, name="models.py", content=ORIGINAL_MODELS):
        """把被审查的原文件放进这个会话的工作区（就像用户上传过它）。"""
        workspace = Path(harness.workspace_dir(ChatSession.objects.get(pk=self.sid)))
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / name).write_bytes(content.encode("utf-8"))

    def send(self, reply=SUGGESTIONS, content="请审查"):
        # TestCase 把每个测试包在一个不提交的事务里，on_commit 的回调永远不会触发；
        # captureOnCommitCallbacks(execute=True) 在退出时把它们执行掉，等价于「事务提交之后」。
        # TransactionTestCase 的事务是真提交的，回调直接就执行了，用不着它（也没有这个方法）。
        capture = getattr(self, "captureOnCommitCallbacks", None)
        with capture(execute=True) if capture else contextlib.nullcontext():
            with mock.patch("apps.harness.get_provider", return_value=RecordingProvider(text(reply))):
                res = self.client.post(f"/api/sessions/{self.sid}/messages/", {"content": content}, format="json")
        assert res.status_code == 201, res.content
        return res.json()

    def drain(self):
        """同步把队列里的运行跑完。"""
        runner = workflow_runtime.get_runner()
        while runner.run_once(block=False):
            pass


class Base(BaseMixin, TestCase):
    """只涉及接口和数据库读写的测试：用 TestCase（每个测试包在回滚的事务里，快）。"""


class ThreadedBase(BaseMixin, TransactionTestCase):
    """runner 的阶段在另一个线程里跑、用另一个数据库连接写库。TestCase 的未提交事务会把表锁住，
    线程写不进去（database table is locked），所以需要线程参与的测试用 TransactionTestCase：数据真的提交。"""


class TriggerTests(Base):
    """eureka 命中后：找到插件 → 创建运行和各阶段 → 入队（不在聊天请求里执行）。"""

    def test_a_hit_creates_a_run_with_one_pending_stage_per_plugin_stage_and_queues_it(self):
        self.send()

        run = WorkflowRun.objects.get()
        assert (run.plugin, run.status) == ("code-review", "pending")
        assert [(s.index, s.key, s.status) for s in run.stages.all()] == [(0, "extract", "pending"), (1, "summarize", "pending"), (2, "report", "pending"), (3, "rewrite", "pending"), (4, "apply", "pending")]
        assert workflow_runtime.get_runner().pending_count() == 1

    def test_the_run_keeps_the_json_that_triggered_it(self):
        self.send()
        assert WorkflowRun.objects.get().payload[0]["sugDesc"] == "问题 A"

    def test_the_chat_reply_returns_immediately_nothing_has_executed_yet(self):
        self.send()
        assert StageRun.objects.filter(status="done").count() == 0 and StageLog.objects.count() == 0

    def test_no_run_when_there_is_no_eureka(self):
        self.send("没有 JSON")
        assert WorkflowRun.objects.count() == 0

    def test_no_run_when_the_plugin_is_not_enabled_in_the_config_but_the_eureka_still_shows(self):
        with override_settings(EUREKA_PLUGINS=""):
            workflow_runtime.reset()
            reply = self.send()
        assert reply[1]["eureka"] == {"text": "🎉 Eureka！", "run": None} and WorkflowRun.objects.count() == 0  # 提示照常显示，只是不展开工作流

    def test_the_same_eureka_is_never_expanded_twice(self):
        self.send()
        eureka = Eureka.objects.get()

        again = workflow_runtime.start_run(Eureka.objects.get(pk=eureka.pk), [{"x": 1}])  # 再触发一次

        assert WorkflowRun.objects.count() == 1 and again.id == WorkflowRun.objects.get().id

    def test_a_broken_workflow_setup_never_breaks_the_chat_reply(self):
        with mock.patch("apps.workflow_runtime.start_run", side_effect=RuntimeError("boom")):
            reply = self.send()
        assert reply[1]["content"] == SUGGESTIONS and reply[1]["eureka"] is not None

    def test_the_run_is_queued_only_after_the_transaction_commits_not_before(self):
        runner = workflow_runtime.get_runner()
        eureka = self.make_eureka_directly()

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            run = workflow_runtime.start_run(eureka, [])
            assert runner.pending_count() == 0  # 事务还没提交：还没入队（否则队列可能先于数据可见）
            assert WorkflowRun.objects.filter(pk=run.id).exists()

        for callback in callbacks:  # 模拟提交
            callback()
        assert runner.pending_count() == 1

    def make_eureka_directly(self):
        session = ChatSession.objects.get(pk=self.sid)
        message = Message.objects.create(session=session, role="assistant", content="[]")
        return Eureka.objects.create(message=message, plugin="code-review", workflow="代码审查.md", text="t")

    def test_the_run_never_enters_the_models_context(self):
        self.send()
        assert "StageRun" not in json.dumps(ChatSession.objects.get(pk=self.sid).conversation, ensure_ascii=False)


class ExecutionTests(ThreadedBase):
    def test_draining_the_queue_runs_all_three_stages_and_marks_everything_done(self):
        self.send()

        self.drain()

        run = WorkflowRun.objects.get()
        assert run.status == "done" and run.finished_at is not None
        assert [s.status for s in run.stages.all()] == ["done"] * 5
        assert all(s.started_at and s.finished_at for s in run.stages.all())

    def test_stage_outputs_are_stored_and_chained(self):
        self.send()
        self.drain()

        out = {s.key: s.output for s in WorkflowRun.objects.get().stages.all()}

        assert len(out["extract"]["suggestions"]) == 2
        assert out["summarize"]["by_type"] == {"QUESTION": 1, "SUGGESTION": 1}
        assert out["report"]["file"].startswith("code-review-report-")

    def test_the_report_file_really_lands_in_the_sessions_workspace(self):
        self.send()
        self.drain()

        filename = StageRun.objects.get(key="report").output["file"]
        session = ChatSession.objects.get(pk=self.sid)

        assert (Path(harness.workspace_dir(session)) / filename).is_file()

    def test_the_log_lines_the_plugin_printed_are_saved_per_stage_in_order(self):
        self.send()
        self.drain()

        logs = {s.key: [log.text for log in s.logs.all()] for s in StageRun.objects.all()}

        assert logs["extract"] == ["提取到 2 条审查建议"]
        assert logs["summarize"] and "QUESTION" in logs["summarize"][0]
        assert "报告已写入工作区" in logs["report"][0]

    def test_a_failing_stage_is_recorded_and_the_rest_stay_pending(self):
        self.send()
        with mock.patch("eureka.plugins.code_review.plugin.CodeReview.reconcile_summarize", side_effect=RuntimeError("汇总炸了")):
            self.drain()

        run = WorkflowRun.objects.get()
        assert run.status == "failed" and "汇总炸了" in run.error
        assert [s.status for s in run.stages.all()] == ["done", "failed", "pending", "pending", "pending"]

    def test_the_workspace_file_is_overwritten_not_duplicated_on_a_second_run_of_the_same_run(self):
        self.send()
        self.drain()
        run = WorkflowRun.objects.get()
        run.status = "running"
        run.save()
        StageRun.objects.filter(run=run, key="report").update(status="pending")

        workflow_runtime.get_runner().enqueue(run.id)
        self.drain()

        files = list(Path(harness.workspace_dir(ChatSession.objects.get(pk=self.sid))).glob("code-review-report-*.md"))
        assert len(files) == 1

    def test_two_sessions_triggering_are_run_one_after_another_in_order(self):
        self.send()
        first = WorkflowRun.objects.get()
        other_sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        from urllib.parse import quote

        self.client.patch(f"/api/sessions/{other_sid}/plugins/{quote('代码审查.md')}/", {"enabled": True}, format="json")
        other_workspace = Path(harness.workspace_dir(ChatSession.objects.get(pk=other_sid)))
        other_workspace.mkdir(parents=True, exist_ok=True)  # 原文件是按会话放的：第二个会话也得有它自己的
        (other_workspace / "models.py").write_bytes(ORIGINAL_MODELS.encode("utf-8"))
        capture = getattr(self, "captureOnCommitCallbacks", None)
        with capture(execute=True) if capture else contextlib.nullcontext():
            with mock.patch("apps.harness.get_provider", return_value=RecordingProvider(text(SUGGESTIONS))):
                self.client.post(f"/api/sessions/{other_sid}/messages/", {"content": "x"}, format="json")

        self.drain()

        runs = list(WorkflowRun.objects.order_by("id"))
        assert [r.status for r in runs] == ["done", "done"] and runs[0].id == first.id


class RecoveryTests(ThreadedBase):
    def test_unfinished_runs_are_requeued_after_a_restart_and_finish(self):
        self.send()
        run = WorkflowRun.objects.get()
        run.status = "running"
        run.save()
        StageRun.objects.filter(run=run, key="extract").update(status="done", output={"suggestions": []})
        workflow_runtime.reset()  # 模拟进程重启：队列空了

        assert workflow_runtime.recover() == 1
        self.drain()

        assert WorkflowRun.objects.get().status == "done"

    def test_recovery_skips_finished_runs(self):
        self.send()
        self.drain()
        workflow_runtime.reset()
        assert workflow_runtime.recover() == 0


class WebhookReceiverTests(Base):
    """POST /api/eureka/runs/<id>/stages/<key>/logs/：令牌 + 回环限制 + 去重。"""

    def setUp(self):
        super().setUp()
        self.send()
        self.run = WorkflowRun.objects.get()
        self.url = f"/api/eureka/runs/{self.run.id}/stages/extract/logs/"
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {run_token(self.run.id)}"}
        self.anon = APIClient()  # 没有会话 cookie：webhook 靠令牌，不靠登录

    def post(self, body, client=None, **headers):
        return (client or self.anon).post(self.url, body, format="json", REMOTE_ADDR="127.0.0.1", **(headers or self.auth))

    def test_a_valid_request_stores_the_line(self):
        res = self.post({"seq": 1, "text": "你好"})

        assert res.status_code == 201
        assert StageLog.objects.get().text == "你好" and StageLog.objects.get().stage.key == "extract"

    def test_no_token_or_a_wrong_token_is_a_403_and_stores_nothing(self):
        assert self.post({"seq": 1, "text": "x"}, HTTP_AUTHORIZATION="").status_code == 403
        assert self.post({"seq": 1, "text": "x"}, HTTP_AUTHORIZATION="Bearer wrong").status_code == 403
        assert self.anon.post(self.url, {"seq": 1, "text": "x"}, format="json", REMOTE_ADDR="127.0.0.1").status_code == 403
        assert StageLog.objects.count() == 0

    def test_a_token_for_another_run_does_not_work(self):
        other = run_token(self.run.id + 1)
        assert self.post({"seq": 1, "text": "x"}, HTTP_AUTHORIZATION=f"Bearer {other}").status_code == 403

    def test_requests_from_outside_the_machine_are_refused_even_with_a_valid_token(self):
        res = self.anon.post(self.url, {"seq": 1, "text": "x"}, format="json", REMOTE_ADDR="203.0.113.9", **self.auth)
        assert res.status_code == 403 and StageLog.objects.count() == 0

    def test_loopback_ipv6_is_accepted_too(self):
        res = self.anon.post(self.url, {"seq": 1, "text": "x"}, format="json", REMOTE_ADDR="::1", **self.auth)
        assert res.status_code == 201

    def test_the_same_seq_sent_twice_is_stored_once_and_both_requests_succeed(self):
        assert self.post({"seq": 1, "text": "x"}).status_code == 201
        assert self.post({"seq": 1, "text": "x"}).status_code == 200  # 重发：已经有了，不报错
        assert StageLog.objects.count() == 1

    def test_the_first_write_for_a_seq_wins(self):
        self.post({"seq": 1, "text": "原文"})
        self.post({"seq": 1, "text": "被篡改的重发"})
        assert StageLog.objects.get().text == "原文"

    def test_bad_bodies_are_a_400(self):
        for body in ({}, {"seq": 0, "text": "x"}, {"seq": -1, "text": "x"}, {"seq": "a", "text": "x"}, {"seq": 1.5, "text": "x"}, {"seq": True, "text": "x"}, {"seq": 1}, {"seq": 1, "text": ""}, {"seq": 1, "text": 5}, {"seq": 1, "text": None}, {"seq": 1, "text": ["x"]}):
            assert self.post(body).status_code == 400, body
        assert StageLog.objects.count() == 0

    def test_an_overlong_line_is_cut_not_stored_whole(self):
        self.post({"seq": 1, "text": "a" * 5000})
        assert len(StageLog.objects.get().text) == 2000

    def test_an_unknown_run_or_stage_is_a_404_but_only_after_the_token_checks_out(self):
        assert self.anon.post("/api/eureka/runs/99999/stages/extract/logs/", {"seq": 1, "text": "x"}, format="json", REMOTE_ADDR="127.0.0.1", HTTP_AUTHORIZATION=f"Bearer {run_token(99999)}").status_code == 404
        assert self.anon.post(f"/api/eureka/runs/{self.run.id}/stages/nope/logs/", {"seq": 1, "text": "x"}, format="json", REMOTE_ADDR="127.0.0.1", **self.auth).status_code == 404

    def test_a_finished_stage_still_accepts_late_lines_but_a_missing_one_does_not(self):
        self.drain()  # 阶段都跑完了：迟到的重发行仍然收下（它们是降级写库之外的补发）
        assert self.post({"seq": 99, "text": "迟到"}).status_code == 201

    def test_get_is_not_allowed(self):
        assert self.anon.get(self.url, REMOTE_ADDR="127.0.0.1", **self.auth).status_code == 405

    def test_the_cap_on_lines_per_stage_is_enforced_on_the_server_too(self):
        with override_settings(EUREKA_MAX_LOG_LINES=2):
            for seq in (1, 2, 3, 4):
                self.post({"seq": seq, "text": f"行{seq}"})
        assert StageLog.objects.count() == 2


class FallbackWriteTests(ThreadedBase):
    """webhook 的降级路径：直接写库，和接收接口用同一套去重规则。"""

    def test_the_direct_write_stores_the_line_and_is_safe_to_repeat(self):
        self.send()
        run = WorkflowRun.objects.get()

        workflow_runtime.write_log_directly(run.id, "extract", 1, "直接写库")
        workflow_runtime.write_log_directly(run.id, "extract", 1, "重复")

        assert [log.text for log in StageLog.objects.all()] == ["直接写库"]


class ProgressApiTests(ThreadedBase):
    def url(self, sid=None, run_id=None):
        return f"/api/sessions/{sid or self.sid}/eureka-runs/{run_id or WorkflowRun.objects.get().id}/"

    def test_progress_lists_the_run_its_stages_and_their_log_lines(self):
        self.send()
        self.drain()

        data = self.client.get(self.url()).json()

        assert data["status"] == "done" and data["plugin"] == "code-review"
        assert [(s["key"], s["status"]) for s in data["stages"]] == [
            ("extract", "done"), ("summarize", "done"), ("report", "done"), ("rewrite", "done"), ("apply", "done"),
        ]  # fmt: skip
        assert [s["title"] for s in data["stages"]][:3] == ["提取审查建议", "汇总", "写出报告文件"]
        assert data["stages"][0]["logs"] == ["提取到 2 条审查建议"]

    def test_progress_while_pending_shows_everything_as_pending(self):
        self.send()
        data = self.client.get(self.url()).json()
        assert data["status"] == "pending" and {s["status"] for s in data["stages"]} == {"pending"}

    def test_a_failed_run_shows_the_error(self):
        self.send()
        with mock.patch("eureka.plugins.code_review.plugin.CodeReview.reconcile_extract", side_effect=RuntimeError("提取炸了")):
            self.drain()
        data = self.client.get(self.url()).json()
        assert data["status"] == "failed" and "提取炸了" in data["error"] and data["stages"][0]["error"]

    def test_the_progress_never_exposes_the_token_or_the_raw_payload(self):
        self.send()
        raw = json.dumps(self.client.get(self.url()).json(), ensure_ascii=False)
        assert run_token(WorkflowRun.objects.get().id) not in raw and "sugDesc" not in raw

    def test_only_the_owner_can_see_it(self):
        self.send()
        other = APIClient()
        assert other.get(self.url()).status_code == 404

    def test_a_run_of_another_session_is_a_404_even_for_the_same_browser(self):
        self.send()
        run_id = WorkflowRun.objects.get().id
        other_sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        assert self.client.get(self.url(sid=other_sid, run_id=run_id)).status_code == 404

    def test_the_message_carries_the_run_so_a_refreshed_page_can_show_progress(self):
        reply = self.send()
        run = WorkflowRun.objects.get()
        assert reply[1]["eureka"]["run"] == {"id": run.id, "status": "pending"}
        self.drain()
        shown = self.client.get(f"/api/sessions/{self.sid}/messages/").json()
        assert shown[1]["eureka"]["run"] == {"id": run.id, "status": "done"}

    def test_an_eureka_without_a_run_has_no_run_key_value(self):
        with override_settings(EUREKA_PLUGINS=""):
            workflow_runtime.reset()
            reply = self.send()
        assert reply[1]["eureka"]["run"] is None


class StartupTests(TestCase):
    """后台线程只在真正的服务进程里启动：manage.py test / migrate / makemigrations 等命令里都不能起线程。"""

    def test_a_server_process_is_recognised_by_its_command(self):
        from apps.apps import is_server_process

        assert is_server_process(["manage.py", "runserver"], env={"RUN_MAIN": "true"})
        assert is_server_process(["manage.py", "runserver", "--noreload"], env={})  # --noreload：没有重载子进程，本进程就是服务

    def test_the_autoreloader_parent_process_does_not_start_the_thread(self):
        from apps.apps import is_server_process

        # runserver 默认会起一个自动重载的父进程（没有 RUN_MAIN）和一个真正提供服务的子进程（RUN_MAIN=true）：只在子进程里启动
        assert not is_server_process(["manage.py", "runserver"], env={})

    def test_other_management_commands_never_start_it(self):
        from apps.apps import is_server_process

        for argv in (["manage.py", "test"], ["manage.py", "migrate"], ["manage.py", "makemigrations"], ["manage.py", "shell"], ["manage.py", "check"], ["manage.py", "push_message"], []):
            assert not is_server_process(argv, env={"RUN_MAIN": "true"}), argv

    def test_a_production_wsgi_or_asgi_server_is_recognised_by_the_explicit_switch(self):
        from apps.apps import is_server_process

        assert is_server_process(["gunicorn", "code_backend.wsgi"], env={"EUREKA_RUNNER": "1"})
        assert not is_server_process(["gunicorn", "code_backend.wsgi"], env={})

    def test_running_the_test_suite_did_not_start_a_background_thread(self):
        import threading

        assert not any(t.name == "eureka-runner" for t in threading.enumerate())

    def test_start_is_safe_to_call_twice(self):
        workflow_runtime.reset()
        try:
            workflow_runtime.start()
            workflow_runtime.start()
            import threading

            assert sum(t.name == "eureka-runner" for t in threading.enumerate()) == 1
        finally:
            workflow_runtime.reset()

    def test_start_recovers_unfinished_runs(self):
        from apps.test_workflow_models import make_run

        run = make_run()
        run.status = "running"
        run.save()
        workflow_runtime.reset()
        try:
            with mock.patch("eureka.runner.Runner.start"):  # 不真的起线程，只看恢复
                workflow_runtime.start()
            assert workflow_runtime.get_runner().pending_count() == 1
        finally:
            workflow_runtime.reset()


class AnalyzeConfigWorkflowTests(ThreadedBase):
    """第二个插件：analyze-config。真实的默认工作流 + 真实的插件，验证两个插件在同一个注册表里互不干扰。"""

    CONFIG_ITEMS = json.dumps(
        [
            {"type": "mysql", "address": "10.0.0.8", "port": "3306", "database": "shop", "username": "root", "password": "pw-secret-1"},
            {"type": "redis", "address": "10.0.0.5:6379,10.0.0.6:6379", "name": "cache", "password": "r-secret-2"},
        ],
        ensure_ascii=False,
    )

    def setUp(self):
        super().setUp()
        base = Path(__file__).resolve().parent.parent
        (self.skills / "分析配置文件.md").write_bytes((base / "default_skills" / "分析配置文件.md").read_bytes())
        (self.flows / "分析配置文件.md").write_bytes((base / "default_workflow" / "分析配置文件.md").read_bytes())
        from urllib.parse import quote

        self.client.patch(f"/api/sessions/{self.sid}/plugins/{quote('代码审查.md')}/", {"enabled": False}, format="json")
        self.client.patch(f"/api/sessions/{self.sid}/plugins/{quote('分析配置文件.md')}/", {"enabled": True}, format="json")
        self.settings_override = override_settings(EUREKA_PLUGINS="code_review, analyze_config")
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        workflow_runtime.reset()

    def test_both_plugins_are_loaded_side_by_side(self):
        assert workflow_runtime.get_registry().names() == ["code-review", "analyze-config"]
        assert workflow_runtime.get_registry().warnings == []

    def test_a_config_report_triggers_the_analyze_config_workflow_with_its_three_stages(self):
        self.send(self.CONFIG_ITEMS, "分析配置")

        run = WorkflowRun.objects.get()

        assert run.plugin == "analyze-config"
        assert [(s.key, s.title) for s in run.stages.all()] == [("extract", "提取配置项"), ("classify", "按类型整理"), ("write", "写入工作区")]

    def test_the_run_executes_and_writes_masked_files_into_the_sessions_workspace(self):
        self.send(self.CONFIG_ITEMS, "分析配置")
        self.drain()

        run = WorkflowRun.objects.get()
        assert run.status == "done" and [s.status for s in run.stages.all()] == ["done"] * 3
        workspace = Path(harness.workspace_dir(ChatSession.objects.get(pk=self.sid)))
        assert sorted(p.name for p in workspace.iterdir() if p.name != "models.py") == [f"analyze-config-{run.id}.json", f"analyze-config-{run.id}.md"]  # models.py 是 setUp 放的原文件
        for path in workspace.iterdir():
            assert "pw-secret-1" not in path.read_text(encoding="utf-8") and "r-secret-2" not in path.read_text(encoding="utf-8")

    def test_the_progress_tells_the_user_the_directory_and_leaks_no_password(self):
        self.send(self.CONFIG_ITEMS, "分析配置")
        self.drain()
        run = WorkflowRun.objects.get()

        data = self.client.get(f"/api/sessions/{self.sid}/eureka-runs/{run.id}/").json()

        logs = [line for stage in data["stages"] for line in stage["logs"]]
        assert any("工作目录" in line for line in logs)
        assert "pw-secret-1" not in json.dumps(data, ensure_ascii=False) and "r-secret-2" not in json.dumps(data, ensure_ascii=False)

    def test_a_code_review_json_does_not_trigger_this_workflow(self):
        self.send(SUGGESTIONS, "审查")  # 勾选的是「分析配置文件」，回复却是审查结果：schema 对不上，不触发
        assert WorkflowRun.objects.count() == 0


class LangChainInjectionTests(TestCase):
    """主项目把 config.ini 里 [eureka] llm_* 的配置注入给 code_review 插件（插件自己不读配置、不 import Django）。"""

    def setUp(self):
        self.override = override_settings(
            EUREKA_PLUGINS="code_review",
            EUREKA_LLM={"api_key": "sk-test-key", "model": "deepseek-flash", "base_url": "https://example.test", "reasoning_effort": "high", "timeout": 12.0},
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        workflow_runtime.reset()
        self.addCleanup(workflow_runtime.reset)

    def test_the_loaded_plugin_gets_a_chain_factory_built_from_the_eureka_llm_settings(self):
        plugin = workflow_runtime.get_registry().get("code-review")

        with mock.patch("eureka.plugins.code_review.plugin.langchain_chain", return_value=lambda inputs: "x") as build:
            plugin.chain_factory()

        build.assert_called_once_with(
            api_key="sk-test-key", model="deepseek-flash", base_url="https://example.test", reasoning_effort="high", timeout=12.0
        )

    def test_the_chain_is_built_lazily_loading_the_registry_never_touches_langchain_or_the_network(self):
        with mock.patch("eureka.plugins.code_review.plugin.langchain_chain") as build:
            workflow_runtime.reset()
            workflow_runtime.get_registry()
        build.assert_not_called()

    def test_without_an_api_key_only_the_rewrite_stage_fails_and_says_why(self):
        with override_settings(EUREKA_LLM={"api_key": "", "model": "m", "base_url": "u", "reasoning_effort": "low", "timeout": 5.0}):
            workflow_runtime.reset()
            plugin = workflow_runtime.get_registry().get("code-review")
            with self.assertRaisesMessage(ValueError, "llm_api_key"):
                plugin.chain_factory()

    def test_a_placeholder_key_counts_as_not_configured(self):
        with override_settings(EUREKA_LLM={"api_key": "sk-your-key-here", "model": "m", "base_url": "u", "reasoning_effort": "low", "timeout": 5.0}):
            workflow_runtime.reset()
            plugin = workflow_runtime.get_registry().get("code-review")
            with self.assertRaisesMessage(ValueError, "llm_api_key"):
                plugin.chain_factory()

    def test_other_plugins_are_not_touched_by_the_injection(self):
        with override_settings(EUREKA_PLUGINS="code_review, analyze_config"):
            workflow_runtime.reset()
            reg = workflow_runtime.get_registry()
        assert reg.names() == ["code-review", "analyze-config"] and reg.warnings == []

    def test_the_settings_fall_back_to_the_deepseek_section_when_the_llm_ones_are_empty(self):
        import configparser

        from code_backend import settings as project_settings

        # 用 config.ini.example 的结构验证回退规则（不读真实的 key）
        parser = configparser.ConfigParser()
        parser.read_string("[deepseek]\napi_key = sk-from-deepseek\nmodel = m1\nbase_url = http://b\n[eureka]\nllm_api_key =\n")
        assert (parser.get("eureka", "llm_api_key").strip() or parser.get("deepseek", "api_key")) == "sk-from-deepseek"
        assert set(project_settings.EUREKA_LLM) == {"api_key", "model", "base_url", "reasoning_effort", "timeout"}

    def test_the_api_key_is_registered_as_a_secret_so_error_messages_never_echo_it(self):
        assert "sk-test-key" in workflow_runtime._secrets()


class RewriteWorkflowTests(ThreadedBase):
    """code-review 的第 4、5 阶段在真实 Django 里：模型用替身，文件真的落进会话工作区。"""

    def workspace(self):
        return Path(harness.workspace_dir(ChatSession.objects.get(pk=self.sid)))

    def test_the_model_receives_the_original_file_and_the_suggestions_of_this_run(self):
        self.send()
        self.drain()

        [call] = self.model_calls
        assert call["filename"] == "models.py" and call["original"] == ORIGINAL_MODELS
        assert "问题 A" in call["suggestions"] and "建议 B" in call["suggestions"]

    def test_the_fixed_file_is_written_next_to_the_original_and_the_original_is_untouched(self):
        self.send()
        self.drain()

        run = WorkflowRun.objects.get()
        fixed = self.workspace() / f"models.fixed-{run.id}.py"
        assert fixed.read_bytes() == FIXED_MODELS.encode("utf-8")
        assert (self.workspace() / "models.py").read_bytes() == ORIGINAL_MODELS.encode("utf-8")

    def test_all_five_stages_finish_and_the_last_ones_report_what_they_did(self):
        self.send()
        self.drain()

        run = WorkflowRun.objects.get()
        stages = {s.key: s for s in run.stages.all()}
        assert run.status == "done" and [s.status for s in run.stages.all()] == ["done"] * 5
        assert stages["rewrite"].output["changed"] is True and stages["apply"].output["written"] is True
        assert any("models.py" in log.text for log in stages["rewrite"].logs.all())
        assert any(f"models.fixed-{run.id}.py" in log.text for log in stages["apply"].logs.all())

    def test_the_progress_response_never_contains_the_file_content_or_the_api_key(self):
        self.send()
        self.drain()
        run = WorkflowRun.objects.get()

        shown = json.dumps(self.client.get(f"/api/sessions/{self.sid}/eureka-runs/{run.id}/").json(), ensure_ascii=False)

        assert "write_only" not in shown and "hashlib" not in shown  # 进度接口不返回阶段输出，所以不会把整份文件内容送给浏览器
        assert "sk-" not in shown

    def test_a_model_failure_fails_only_the_rewrite_stage_and_nothing_is_written(self):
        self.send()
        with mock.patch("eureka.plugins.code_review.plugin.langchain_chain", side_effect=RuntimeError("deepseek down")):
            self.drain()

        run = WorkflowRun.objects.get()
        assert run.status == "failed" and "deepseek down" in run.error
        assert [s.status for s in run.stages.all()] == ["done", "done", "done", "failed", "pending"]
        assert not list(self.workspace().glob("*.fixed-*"))  # 模型没成功，就没有新文件
        assert (self.workspace() / "models.py").read_bytes() == ORIGINAL_MODELS.encode("utf-8")

    def test_the_api_key_is_never_shown_even_if_the_model_error_contains_it(self):
        key = "sk-leaky-key-9999"
        with override_settings(EUREKA_LLM={"api_key": key, "model": "m", "base_url": "u", "reasoning_effort": "low", "timeout": 5.0}):
            workflow_runtime.reset()  # 先换配置再触发：reset 会清掉队列
            self.send()
            with mock.patch("eureka.plugins.code_review.plugin.langchain_chain", side_effect=RuntimeError(f"401 for key {key}")):
                self.drain()
            run = WorkflowRun.objects.get()
            data = self.client.get(f"/api/sessions/{self.sid}/eureka-runs/{run.id}/").json()
        assert key not in json.dumps(data, ensure_ascii=False) and key not in run.error and "***" in run.error

    def test_a_missing_original_file_fails_the_rewrite_stage_with_a_clear_message(self):
        self.send()
        (self.workspace() / "models.py").unlink()

        self.drain()

        run = WorkflowRun.objects.get()
        assert run.status == "failed" and "models.py" in run.error and "上传" in run.error

    def test_the_model_saying_nothing_needs_changing_writes_no_new_file(self):
        self.model_reply = ORIGINAL_MODELS
        self.send()
        self.drain()

        run = WorkflowRun.objects.get()
        assert run.status == "done" and not list(self.workspace().glob("*.fixed-*"))
        assert {s.key: s for s in run.stages.all()}["apply"].output["written"] is False

    def test_rerunning_after_a_failure_resumes_at_rewrite_without_redoing_the_first_three_stages(self):
        self.send()
        with mock.patch("eureka.plugins.code_review.plugin.langchain_chain", side_effect=RuntimeError("temporary")):
            self.drain()
        run = WorkflowRun.objects.get()
        report = self.workspace() / f"code-review-report-{run.id}.md"
        report_mtime = report.stat().st_mtime_ns

        WorkflowRun.objects.filter(pk=run.pk).update(status="running", error="")  # 模拟重启恢复
        StageRun.objects.filter(run=run, key="rewrite").update(status="running", error="")
        workflow_runtime.recover()
        self.drain()

        run.refresh_from_db()
        assert run.status == "done" and report.stat().st_mtime_ns == report_mtime  # 报告没被重写
        assert (self.workspace() / f"models.fixed-{run.id}.py").is_file()
