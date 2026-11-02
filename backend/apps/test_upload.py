"""文件上传接口测试：文件保存到 <存储路径>/input/<会话id>/file/，和模型的工具共用同一个目录。"""

import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps import harness, services
from apps.models import ChatSession
from apps.test_harness import FakeProvider, calls, run, text
from providers import ToolCall


class UploadTests(TestCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.storage = Path(storage.name)
        override = override_settings(HARNESS_STORAGE_DIR=storage.name, HARNESS_MAX_UPLOAD_BYTES=1024)
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        self.url = f"/api/sessions/{self.sid}/files/"
        self.workspace = self.storage / "input" / self.sid / "file"

    def upload(self, name="a.txt", content=b"hello", client=None, field="file"):
        return (client or self.client).post(self.url, {field: SimpleUploadedFile(name, content)}, format="multipart")

    def test_file_is_saved_in_the_sessions_directory_and_the_response_says_where(self):
        res = self.upload("读我.txt", "你好".encode())

        assert res.status_code == 201
        assert res.json() == {"name": "读我.txt", "size": len("你好".encode())}
        assert (self.workspace / "读我.txt").read_bytes() == "你好".encode()

    def test_binary_content_is_stored_unchanged(self):
        data = bytes(range(256)) * 3

        self.upload("blob.bin", data)

        assert (self.workspace / "blob.bin").read_bytes() == data

    def test_path_in_the_file_name_is_dropped_so_it_cannot_escape_the_directory(self):
        for evil in ["../../evil.txt", "..\\..\\evil.txt", "/etc/evil.txt", "sub/dir/evil.txt"]:
            res = self.upload(evil)
            assert res.status_code == 201 and res.json()["name"].startswith("evil"), evil

        assert not (self.storage / "evil.txt").exists() and not (self.storage.parent / "evil.txt").exists()
        assert all(p.parent == self.workspace for p in self.workspace.iterdir())  # 全在工作区这一层

    def test_unusable_file_names_are_rejected(self):
        # "." ".." "a/" 这类名字，Django 解析上传时自己就拦了（SimpleUploadedFile 构造都会抛），到不了我们的代码；
        # 能到的是全空白的名字，直接对清理函数验证其余几种
        assert self.upload("   ").status_code == 400
        assert not self.workspace.exists()
        for bad in ["", ".", "..", "a/..", "/", " / "]:
            with self.assertRaises(services.UploadError, msg=repr(bad)):
                services._safe_file_name(bad)

    def test_same_name_is_renamed_and_never_overwrites_an_existing_file(self):
        self.upload("a.txt", b"first")

        res = self.upload("a.txt", b"second")

        assert res.json()["name"] == "a (1).txt"
        assert (self.workspace / "a.txt").read_bytes() == b"first"
        assert (self.workspace / "a (1).txt").read_bytes() == b"second"
        assert self.upload("a.txt", b"third").json()["name"] == "a (2).txt"

    def test_missing_file_field_is_a_400(self):
        assert self.upload(field="other").status_code == 400
        assert self.client.post(self.url, {}, format="multipart").status_code == 400

    def test_too_large_file_is_a_413_and_nothing_is_kept(self):
        res = self.upload("big.bin", b"x" * 1025)

        assert res.status_code == 413
        assert not (self.workspace / "big.bin").exists()

    def test_file_exactly_at_the_limit_is_accepted(self):
        assert self.upload("edge.bin", b"x" * 1024).status_code == 201

    def test_other_browser_gets_404_and_nothing_is_written(self):
        assert self.upload(client=APIClient()).status_code == 404
        assert not self.workspace.exists()

    def test_unknown_session_is_a_404(self):
        url = "/api/sessions/00000000-0000-0000-0000-000000000000/files/"
        assert self.client.post(url, {"file": SimpleUploadedFile("a.txt", b"x")}, format="multipart").status_code == 404

    def test_get_is_not_allowed(self):
        assert self.client.get(self.url).status_code == 405

    def test_model_can_list_and_read_the_uploaded_file_in_its_next_turn(self):
        self.upload("data.txt", "上传的内容".encode())
        session = ChatSession.objects.get(pk=self.sid)
        provider = FakeProvider(
            calls(ToolCall("c1", "list_files", {}), ToolCall("c2", "read_file", {"path": "data.txt"})), text("看到了")
        )

        run(provider, session=session)

        listing, content = [m["content"] for m in provider.calls[1]["conversation"] if m["role"] == "tool"][-2:]
        assert "data.txt" in listing and "上传的内容" in content

    def test_upload_goes_to_the_same_directory_the_tools_use(self):
        self.upload("x.txt")

        assert Path(harness.workspace_dir(ChatSession.objects.get(pk=self.sid))) == self.workspace
        assert (self.workspace / "x.txt").exists()
