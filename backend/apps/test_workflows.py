"""工作流接口测试：规则和插件一样，文件存在 <存储路径>/input/<会话id>/workflow/<文件名>.md，frontmatter 要有 name、description、schema。"""

import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps import workflows
from apps.models import ChatSession
from apps.services import PluginError


VALID = '---\nname: flow\ndescription: 示例\nschema: {"type": "object"}\n---\n# 流程\n'


def md(name="flow.md", content=VALID):
    return SimpleUploadedFile(name, content.encode("utf-8"))


class WorkflowApiTests(TestCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        default = tempfile.TemporaryDirectory()
        self.addCleanup(default.cleanup)
        self.default_dir = Path(default.name)
        override = override_settings(HARNESS_STORAGE_DIR=storage.name, HARNESS_DEFAULT_WORKFLOW_DIR=default.name)
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        self.base = f"/api/sessions/{self.sid}/workflows/"
        self.dir = Path(storage.name) / "input" / self.sid / "workflow"

    def upload(self, file=None):
        return self.client.post(self.base, {"file": file or md()}, format="multipart")

    def test_upload_saves_file_as_is_and_lists_it(self):
        res = self.upload()

        assert res.status_code == 201
        assert res.json() == {"name": "flow.md", "enabled": False, "default": False}
        assert (self.dir / "flow.md").read_text(encoding="utf-8") == VALID
        assert self.client.get(self.base).json() == [{"name": "flow.md", "enabled": False, "default": False}]

    def test_detail_returns_full_content_for_own_and_default(self):
        self.upload()
        (self.default_dir / "base.md").write_text("# 默认", encoding="utf-8")

        own = self.client.get(f"{self.base}flow.md/").json()
        default = self.client.get(f"{self.base}base.md/").json()

        assert own["content"] == VALID and own["default"] is False
        assert default["content"] == "# 默认" and default["default"] is True
        assert self.client.get(f"{self.base}nope.md/").status_code == 404

    def test_bad_frontmatter_is_400_and_nothing_saved(self):
        bad = [
            "# 没有 frontmatter\n",
            "---\ndescription: x\nschema: {}\n---\n",
            "---\nname: Bad_Name\ndescription: x\nschema: {}\n---\n",
            "---\nname: flow\nschema: {}\n---\n",
            "---\nname: flow\ndescription: x\n---\n",
            "---\nname: flow\ndescription: x\nschema: 不是json\n---\n",
            "---\nname: flow\ndescription: x\nschema: [1]\n---\n",
        ]
        for content in bad:
            assert self.upload(md(content=content)).status_code == 400, content
        assert not self.dir.exists() or not list(self.dir.glob("*.md"))

    def test_non_md_is_rejected(self):
        assert self.upload(md("a.txt")).status_code == 400

    def test_duplicate_is_409(self):
        self.upload()
        assert self.upload().status_code == 409

    def test_toggle_and_delete(self):
        self.upload()
        url = f"{self.base}flow.md/"

        assert self.client.patch(url, {"enabled": True}, format="json").json()["enabled"] is True
        assert self.client.get(self.base).json()[0]["enabled"] is True
        assert self.client.delete(url).status_code == 204
        assert self.client.get(self.base).json() == []

    def test_default_workflow_is_listed_checkable_but_not_deletable_or_overridable(self):
        (self.default_dir / "base.md").write_text("# 默认", encoding="utf-8")

        assert self.client.get(self.base).json() == [{"name": "base.md", "enabled": False, "default": True}]
        url = f"{self.base}base.md/"
        assert self.client.patch(url, {"enabled": True}, format="json").json()["enabled"] is True
        assert self.client.delete(url).status_code == 403
        assert self.upload(md("base.md")).status_code == 409

    def test_path_traversal_name_is_404(self):
        # 直接测业务层：带 .. 的路径在 URL 层会被 Django 当成 404 页面，测试客户端对它会崩（插件的测试也是这样）
        session = ChatSession.objects.get(pk=self.sid)
        for evil in ["..", "../keep.md", "..\keep.md", "state.json", ""]:
            with self.assertRaises(PluginError) as e:
                workflows.delete_workflow(session, evil)
            assert e.exception.status == 404, evil

    def test_changes_are_handed_to_core_hook(self):
        with mock.patch.object(workflows, "apply_to_core") as hook:
            self.upload()
            self.client.patch(f"{self.base}flow.md/", {"enabled": True}, format="json")
            self.client.delete(f"{self.base}flow.md/")

        assert [c.args[0] for c in hook.call_args_list] == ["created", "updated", "deleted"]
