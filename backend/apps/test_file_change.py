"""文件变更提示：这一轮工作区有文件创建或变更时，在那条回复下面多一条提示（和 eureka 同一套逻辑，单独一张表）。"""

import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps import harness, services
from apps.models import ChatSession, FileChange


class FileChangeTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(HARNESS_STORAGE_DIR=tmp.name)
        override.enable()
        self.addCleanup(override.disable)
        self.session = ChatSession.objects.create(owner_key="k")
        self.root = Path(harness.workspace_dir(self.session))

    def reply(self):
        self.session.refresh_from_db()
        return services.save_assistant_message(self.session, "好的")

    def test_no_files_no_hint(self):
        self.assertFalse(hasattr(self.reply(), "file_change"))
        self.assertEqual(FileChange.objects.count(), 0)

    def test_created_then_unchanged_then_modified(self):
        self.root.mkdir(parents=True)
        (self.root / "a.txt").write_text("1")
        self.assertEqual(self.reply().file_change.files, [{"path": "a.txt", "kind": "created"}])

        self.assertFalse(hasattr(self.reply(), "file_change"))  # 没变化：没有提示

        (self.root / "a.txt").write_text("22")
        self.assertEqual(self.reply().file_change.files, [{"path": "a.txt", "kind": "modified"}])


class WorkspaceBrowseTests(TestCase):
    """工作区文件列表和查看内容的接口。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(HARNESS_STORAGE_DIR=tmp.name)
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.session = ChatSession.objects.create(owner_key=self.client.get("/api/sessions/").wsgi_request.session.session_key)
        root = Path(harness.workspace_dir(self.session))
        (root / "sub").mkdir(parents=True)
        (root / "a.txt").write_text("你好", encoding="utf-8")
        (root / "sub" / "b.txt").write_text("B", encoding="utf-8")
        (Path(tmp.name) / "secret.txt").write_text("S")

    def url(self, tail=""):
        return f"/api/sessions/{self.session.id}/workspace/{tail}"

    def test_list(self):
        data = self.client.get(self.url()).json()
        self.assertEqual([f["path"] for f in data], ["a.txt", "sub/b.txt"])

    def test_read(self):
        data = self.client.get(self.url("file/"), {"path": "a.txt"}).json()
        self.assertEqual(data["content"], "你好")

    def test_cannot_escape_workspace(self):
        for p in ["../../../secret.txt", "..\\..\\secret.txt", "nope.txt", ""]:
            self.assertEqual(self.client.get(self.url("file/"), {"path": p}).status_code, 404, p)


class WorkspaceEditTests(WorkspaceBrowseTests):
    """编辑工作区文件：PUT 覆盖已有文件的内容。"""

    def put(self, path, content):
        return self.client.put(self.url("file/"), {"path": path, "content": content}, format="json")

    def test_edit_existing(self):
        self.assertEqual(self.put("sub/b.txt", "新内容").status_code, 200)
        self.assertEqual(self.client.get(self.url("file/"), {"path": "sub/b.txt"}).json()["content"], "新内容")

    def test_edit_does_not_count_as_ai_change(self):
        """用户自己改的文件，不该在下一条回复下面提示「有文件变更」。"""
        self.session.refresh_from_db()
        services.save_assistant_message(self.session, "好的")  # 先把快照对齐
        self.put("a.txt", "改过了")
        self.session.refresh_from_db()
        self.assertFalse(hasattr(services.save_assistant_message(self.session, "好的"), "file_change"))

    def test_cannot_edit_outside_or_missing(self):
        for p in ["../../../secret.txt", "..\\..\\secret.txt", "nope.txt", ""]:
            self.assertEqual(self.put(p, "x").status_code, 404, p)

    def test_too_large(self):
        self.assertEqual(self.put("a.txt", "x" * (services.MAX_VIEW_BYTES + 1)).status_code, 413)
