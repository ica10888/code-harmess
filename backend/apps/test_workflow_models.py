"""工作流运行的数据模型：WorkflowRun / StageRun / StageLog。

和 Eureka 一样，只是展示和进度用的表，不进模型上下文。状态写回数据库，刷新页面、重启服务后还知道做到哪一步。
"""

import secrets

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.models import ChatSession, Eureka, Message, StageLog, StageRun, WorkflowRun


def make_eureka():
    session = ChatSession.objects.create(owner_key="k")
    message = Message.objects.create(session=session, role="assistant", content="[]")
    return Eureka.objects.create(message=message, plugin="code-review", workflow="代码审查.md", text="t")


def make_run(**kw):
    eureka = kw.pop("eureka", None) or make_eureka()
    return WorkflowRun.objects.create(eureka=eureka, plugin="code-review", payload=kw.pop("payload", [{"a": 1}]), **kw)


class WorkflowRunModelTests(TestCase):
    def test_a_new_run_starts_pending_with_no_error_and_no_finish_time(self):
        run = make_run()
        assert (run.status, run.error, run.finished_at) == ("pending", "", None)
        assert run.created_at is not None

    def test_the_payload_is_stored_as_json(self):
        payload = [{"startLineNum": 1, "sugDesc": "中文 ✓"}]
        run = make_run(payload=payload)
        assert WorkflowRun.objects.get(pk=run.pk).payload == payload

    def test_one_eureka_has_at_most_one_run(self):
        eureka = make_eureka()
        make_run(eureka=eureka)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_run(eureka=eureka)  # 同一条 eureka 只展开一次（幂等）

    def test_the_eureka_can_reach_its_run_and_deleting_the_eureka_deletes_the_run(self):
        run = make_run()
        assert run.eureka.run == run
        run.eureka.delete()
        assert WorkflowRun.objects.count() == 0

    def test_status_choices_are_pending_running_done_failed(self):
        assert [c[0] for c in WorkflowRun.STATUS_CHOICES] == ["pending", "running", "done", "failed"]

    def test_the_run_knows_its_session_through_the_eureka(self):
        run = make_run()
        assert run.session == run.eureka.message.session


class StageRunModelTests(TestCase):
    def test_stages_are_ordered_by_index_and_start_pending(self):
        run = make_run()
        StageRun.objects.create(run=run, index=1, key="report", title="报告", method="reconcile_report")
        StageRun.objects.create(run=run, index=0, key="extract", title="提取", method="reconcile_extract")

        stages = list(run.stages.all())

        assert [s.key for s in stages] == ["extract", "report"]
        assert all(s.status == "pending" and s.output == {} and s.error == "" for s in stages)

    def test_index_is_unique_within_a_run_but_not_across_runs(self):
        run, other = make_run(), make_run()
        StageRun.objects.create(run=run, index=0, key="a", title="A", method="m")
        StageRun.objects.create(run=other, index=0, key="a", title="A", method="m")  # 另一个 run：可以
        with self.assertRaises(IntegrityError), transaction.atomic():
            StageRun.objects.create(run=run, index=0, key="b", title="B", method="m")

    def test_key_is_unique_within_a_run(self):
        run = make_run()
        StageRun.objects.create(run=run, index=0, key="a", title="A", method="m")
        with self.assertRaises(IntegrityError), transaction.atomic():
            StageRun.objects.create(run=run, index=1, key="a", title="A2", method="m")

    def test_the_stage_keeps_a_snapshot_of_the_plugins_stage_declaration(self):
        stage = StageRun.objects.create(run=make_run(), index=0, key="x", title="提取审查结果", method="reconcile_x")
        assert (stage.title, stage.method) == ("提取审查结果", "reconcile_x")  # 以后插件改了阶段，老记录不变

    def test_output_is_stored_as_json(self):
        stage = StageRun.objects.create(run=make_run(), index=0, key="x", title="X", method="m", output={"n": 2, "名": "值"})
        assert StageRun.objects.get(pk=stage.pk).output == {"n": 2, "名": "值"}

    def test_deleting_the_run_deletes_its_stages(self):
        run = make_run()
        StageRun.objects.create(run=run, index=0, key="a", title="A", method="m")
        run.delete()
        assert StageRun.objects.count() == 0


class StageLogModelTests(TestCase):
    def setUp(self):
        self.stage = StageRun.objects.create(run=make_run(), index=0, key="a", title="A", method="m")

    def test_logs_are_ordered_by_seq(self):
        StageLog.objects.create(stage=self.stage, seq=2, text="第二行")
        StageLog.objects.create(stage=self.stage, seq=1, text="第一行")
        assert [log.text for log in self.stage.logs.all()] == ["第一行", "第二行"]

    def test_seq_is_unique_within_a_stage_so_a_resend_cannot_duplicate_a_line(self):
        StageLog.objects.create(stage=self.stage, seq=1, text="x")
        with self.assertRaises(IntegrityError), transaction.atomic():
            StageLog.objects.create(stage=self.stage, seq=1, text="x 的重发")

    def test_deleting_the_stage_deletes_its_logs(self):
        StageLog.objects.create(stage=self.stage, seq=1, text="x")
        self.stage.delete()
        assert StageLog.objects.count() == 0


class LogCapTests(TestCase):
    """日志不能无限增长：单条和单阶段都有上限（方案 §8）。"""

    def test_next_seq_counts_from_one_and_follows_the_last_line(self):
        stage = StageRun.objects.create(run=make_run(), index=0, key="a", title="A", method="m")
        assert stage.next_log_seq() == 1
        StageLog.objects.create(stage=stage, seq=1, text="x")
        StageLog.objects.create(stage=stage, seq=5, text="y")
        assert stage.next_log_seq() == 6


class NoContextLeakTests(TestCase):
    def test_none_of_these_tables_touch_the_conversation_the_model_sees(self):
        run = make_run()
        session = run.eureka.message.session
        StageRun.objects.create(run=run, index=0, key="a", title="A", method="m")
        assert ChatSession.objects.get(pk=session.pk).conversation == []


class RunTokenTests(TestCase):
    """webhook 的令牌：由 SECRET_KEY 对 run_id 做 HMAC 派生，不存库（方案 §13）。"""

    def test_the_token_is_stable_for_the_same_run_and_differs_between_runs(self):
        from apps.workflows_run import run_token

        assert run_token(1) == run_token(1)
        assert run_token(1) != run_token(2)

    def test_a_token_is_long_and_not_guessable_from_the_run_id(self):
        from apps.workflows_run import run_token

        token = run_token(7)
        assert len(token) >= 32 and "7" != token and token != secrets.token_hex(16)

    def test_verify_accepts_the_right_token_only_for_its_own_run(self):
        from apps.workflows_run import run_token, verify_run_token

        assert verify_run_token(1, run_token(1))
        assert not verify_run_token(2, run_token(1))
        assert not verify_run_token(1, "")
        assert not verify_run_token(1, "x" * 64)
        assert not verify_run_token(1, None)

    def test_the_token_changes_if_the_secret_key_changes(self):
        from django.test import override_settings

        from apps.workflows_run import run_token

        before = run_token(1)
        with override_settings(SECRET_KEY="another-secret"):
            assert run_token(1) != before


class SqliteConcurrencyTests(TestCase):
    """后台线程写库和请求线程读写库同时发生（runner 就是这样用数据库的）：配置要让它们不互相卡死。"""

    def test_the_database_is_configured_for_a_writer_thread_alongside_readers(self):
        from django.conf import settings

        options = settings.DATABASES["default"].get("OPTIONS", {})
        assert options.get("timeout", 0) >= 10  # 遇到锁先等，不要立刻报 database is locked
        # WAL 在连接建立时由 code_backend.sqlite 设置（Django 4.2 没有 init_command），见下一个测试

    def test_a_real_file_database_survives_a_background_writer_and_a_foreground_writer_at_once(self):
        import sqlite3
        import tempfile
        import threading
        from pathlib import Path

        from code_backend.sqlite import apply_pragmas

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.sqlite3"
            errors, done = [], []

            def writer(tag, n):
                conn = sqlite3.connect(path, timeout=20)
                apply_pragmas(conn)
                try:
                    for i in range(n):
                        conn.execute("INSERT INTO t(tag, i) VALUES (?, ?)", (tag, i))
                        conn.commit()
                    done.append(tag)
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))
                finally:
                    conn.close()

            init = sqlite3.connect(path)
            apply_pragmas(init)
            init.execute("CREATE TABLE t(tag TEXT, i INTEGER)")
            init.commit()
            init.close()

            threads = [threading.Thread(target=writer, args=(tag, 200)) for tag in ("a", "b", "c")]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            check = sqlite3.connect(path)
            total = check.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            mode = check.execute("PRAGMA journal_mode").fetchone()[0]
            check.close()

        assert errors == [] and sorted(done) == ["a", "b", "c"]
        assert total == 600  # 三个线程各写 200 行，一行都没丢
        assert mode == "wal"
