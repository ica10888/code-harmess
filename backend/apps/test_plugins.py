"""插件管理接口测试：每个会话一份，任何 .md 文件都可以，原样存在 <存储路径>/input/<会话id>/skills/<文件名>。

增删改查都落在文件上，没有数据库表。「是否勾选」存在 skills/state.json 里。
对文件内容没有任何要求（不需要 frontmatter，也不要求是 UTF-8）；唯一的条件是文件名以 .md 结尾。
只有「把插件交给 core」这一步（services.apply_to_core）是空方法，这里只验证它被调用。
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps import harness, services
from apps.models import ChatSession


def md(name="notes.md", content="# 随便写点什么\n\n没有 frontmatter 也行。\n"):
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    return SimpleUploadedFile(name, data)


class PluginApiTests(TestCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.storage = Path(storage.name)
        override = override_settings(HARNESS_STORAGE_DIR=storage.name, HARNESS_MAX_UPLOAD_BYTES=1024)
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        self.base = f"/api/sessions/{self.sid}/plugins/"
        self.skills_dir = self.storage / "input" / self.sid / "skills"

    def upload(self, file=None, client=None, base=None):
        return (client or self.client).post(base or self.base, {"file": file or md()}, format="multipart")

    # --- 增：只要是 .md ---

    def test_any_md_file_is_accepted_and_saved_under_its_own_name(self):
        res = self.upload(md("notes.md"))

        assert res.status_code == 201
        assert res.json() == {"name": "notes.md", "enabled": False}
        assert (self.skills_dir / "notes.md").read_text(encoding="utf-8") == "# 随便写点什么\n\n没有 frontmatter 也行。\n"

    def test_no_frontmatter_empty_and_binary_content_are_all_fine(self):
        for name, content in [("plain.md", "just text"), ("empty.md", ""), ("bin.md", b"\xff\xfe\x00\x01")]:
            assert self.upload(md(name, content)).status_code == 201, name
        assert (self.skills_dir / "bin.md").read_bytes() == b"\xff\xfe\x00\x01"

    def test_the_file_is_stored_byte_for_byte(self):
        body = "中文 ✓\r\n第二行\r\n".encode("utf-8")
        self.upload(md("a.md", body))
        assert (self.skills_dir / "a.md").read_bytes() == body

    def test_new_plugin_is_not_checked_by_default(self):
        assert self.upload().json()["enabled"] is False

    def test_chinese_spaces_and_dots_in_the_name_are_fine(self):
        for name in ["我的笔记.md", "my notes.md", "v1.2.final.md", "UPPER.MD", "a-b_c.md"]:
            assert self.upload(md(name)).status_code == 201, name
        assert (self.skills_dir / "我的笔记.md").exists()

    def test_files_that_are_not_md_are_rejected_and_nothing_is_written(self):
        for name in ["a.txt", "a.markdown", "a", "a.md.exe", "a.mdx", ".md", " .md", "..md"]:
            res = self.upload(md(name))
            assert res.status_code == 400, name
            assert ".md" in res.json()["detail"], name
        assert not self.skills_dir.exists()

    def test_names_the_server_cannot_save_are_rejected_with_400_not_500(self):
        # 冒号不在这里：Windows 上 Django 把 "a:b.md" 当成「盘符 a: + 文件名 b.md」，到我们手里已经是 "b.md"
        for name in ["a*b.md", "a?b.md", "a<b.md", 'a"b.md', "a|b.md", "CON.md", "nul.MD", "com1.md", "x" * 300 + ".md"]:
            assert self.upload(md(name)).status_code == 400, name
        assert not self.skills_dir.exists()

    def test_a_colon_in_the_name_is_rejected_by_the_business_layer(self):
        session = ChatSession.objects.get(pk=self.sid)
        fake = mock.Mock(read=lambda: b"x", size=1)
        fake.name = "a:b.md"  # 不经过 Django 的文件名处理，直接给业务层
        with self.assertRaises(services.PluginError) as e:
            services.create_plugin(session, fake)
        assert e.exception.status == 400

    def test_the_directory_part_of_the_name_is_dropped_so_it_cannot_escape(self):
        # SimpleUploadedFile 自己会取文件名的最后一段；走业务层再验证一遍，防止请求手写
        session = ChatSession.objects.get(pk=self.sid)
        for evil in ["../evil.md", "..\\evil.md", "/abs/evil.md", "a/b/evil.md"]:
            fake = mock.Mock(read=lambda: b"x", size=1)
            fake.name = evil  # Mock 的 name 参数是保留的，只能赋值
            plugin = services.create_plugin(session, fake)
            assert plugin["name"] == "evil.md", evil
            services.delete_plugin(session, "evil.md")
        assert not (self.storage / "evil.md").exists() and not (self.storage / "input" / "evil.md").exists()

    def test_same_name_in_the_same_session_is_a_409_and_the_file_is_not_overwritten(self):
        self.upload(md("a.md", "第一版"))

        res = self.upload(md("a.md", "第二版"))

        assert res.status_code == 409 and "a.md" in res.json()["detail"]
        assert (self.skills_dir / "a.md").read_text(encoding="utf-8") == "第一版"

    def test_size_limit_is_the_shared_upload_limit(self):
        assert self.upload(md("ok.md", b"x" * 1024)).status_code == 201  # 刚好等于上限
        assert self.upload(md("big.md", b"x" * 1025)).status_code == 413
        assert not (self.skills_dir / "big.md").exists()

    def test_missing_file_field_is_a_400(self):
        assert self.client.post(self.base, {}, format="multipart").status_code == 400

    def test_state_json_can_never_be_clobbered_by_an_upload(self):
        # 插件只能是 .md，所以和 state.json 不可能同名
        assert self.upload(md("state.json")).status_code == 400

    # --- 查 ---

    def test_list_returns_the_sessions_plugins_ordered_by_name(self):
        for name in ["zeta.md", "alpha.md", "mid.md"]:
            self.upload(md(name))

        res = self.client.get(self.base)

        assert res.status_code == 200
        assert [p["name"] for p in res.json()] == ["alpha.md", "mid.md", "zeta.md"]
        assert set(res.json()[0]) == {"name", "enabled"}  # 列表里不带正文

    def test_empty_session_has_an_empty_list_and_listing_does_not_create_the_directory(self):
        assert self.client.get(self.base).json() == []
        assert not self.skills_dir.exists()

    def test_detail_returns_the_full_content_even_for_non_utf8_files(self):
        self.upload(md("a.md", "全文内容"))
        self.upload(md("bin.md", b"ok\xff\xfeok"))

        assert self.client.get(f"{self.base}a.md/").json()["content"] == "全文内容"
        res = self.client.get(f"{self.base}bin.md/")
        assert res.status_code == 200 and res.json()["content"].startswith("ok")  # 解不了的字节用 � 代替，不崩

    def test_detail_works_for_names_that_need_url_encoding(self):
        self.upload(md("我的 笔记.md", "x"))
        from urllib.parse import quote

        assert self.client.get(f"{self.base}{quote('我的 笔记.md')}/").status_code == 200

    def test_only_md_files_are_listed_and_junk_in_the_directory_is_ignored(self):
        self.upload(md("good.md"))
        (self.skills_dir / "readme.txt").write_text("x")
        (self.skills_dir / "folder.md").mkdir()  # 名字像 md 的目录不算
        (self.skills_dir / "sub").mkdir()
        (self.skills_dir / "sub" / "inner.md").write_text("x")  # 子目录里的不算

        assert [p["name"] for p in self.client.get(self.base).json()] == ["good.md"]

    # --- 每个会话一份 ---

    def test_each_session_has_its_own_plugin_list(self):
        other_sid = self.client.post("/api/sessions/", {}, format="json").json()["id"]
        self.upload()

        assert self.client.get(f"/api/sessions/{other_sid}/plugins/").json() == []
        assert self.upload(base=f"/api/sessions/{other_sid}/plugins/").status_code == 201  # 同名也可以
        assert (self.storage / "input" / other_sid / "skills" / "notes.md").exists()

    def test_other_browser_cannot_touch_my_sessions_plugins(self):
        self.upload()
        other = APIClient()
        item = f"{self.base}notes.md/"

        assert other.get(self.base).status_code == 404
        assert self.upload(client=other).status_code == 404
        assert other.get(item).status_code == 404
        assert other.patch(item, {"enabled": True}, format="json").status_code == 404
        assert other.delete(item).status_code == 404
        assert (self.skills_dir / "notes.md").exists()

    def test_unknown_session_is_a_404(self):
        assert self.client.get("/api/sessions/00000000-0000-0000-0000-000000000000/plugins/").status_code == 404

    # --- 改（勾选 / 取消勾选）---

    def test_patch_toggles_enabled_and_it_is_remembered(self):
        self.upload()
        item = f"{self.base}notes.md/"

        on = self.client.patch(item, {"enabled": True}, format="json")
        assert on.status_code == 200 and on.json() == {"name": "notes.md", "enabled": True}
        assert [p["enabled"] for p in self.client.get(self.base).json()] == [True]

        assert self.client.patch(item, {"enabled": False}, format="json").json()["enabled"] is False
        assert [p["enabled"] for p in self.client.get(self.base).json()] == [False]

    def test_enabled_state_is_kept_in_a_json_file_next_to_the_plugins(self):
        self.upload()
        self.client.patch(f"{self.base}notes.md/", {"enabled": True}, format="json")

        assert json.loads((self.skills_dir / "state.json").read_text(encoding="utf-8")) == {"notes.md": True}

    def test_patch_rejects_non_boolean_and_cannot_rename(self):
        self.upload()
        item = f"{self.base}notes.md/"
        assert self.client.patch(item, {"enabled": "maybe"}, format="json").status_code == 400
        assert self.client.patch(item, {}, format="json").status_code == 400
        self.client.patch(item, {"name": "hacked.md", "enabled": True}, format="json")
        assert (self.skills_dir / "notes.md").exists() and not (self.skills_dir / "hacked.md").exists()

    def test_a_corrupt_state_file_is_treated_as_nothing_checked(self):
        self.upload()
        (self.skills_dir / "state.json").write_text("{not json", encoding="utf-8")
        assert [p["enabled"] for p in self.client.get(self.base).json()] == [False]
        assert self.client.patch(f"{self.base}notes.md/", {"enabled": True}, format="json").status_code == 200

    # --- 删 ---

    def test_delete_removes_the_file_and_its_checked_state(self):
        self.upload()
        item = f"{self.base}notes.md/"
        self.client.patch(item, {"enabled": True}, format="json")

        assert self.client.delete(item).status_code == 204

        assert not (self.skills_dir / "notes.md").exists()
        assert "notes.md" not in json.loads((self.skills_dir / "state.json").read_text(encoding="utf-8"))
        assert self.client.get(self.base).json() == []

    def test_deleting_twice_or_an_unknown_name_is_a_404(self):
        self.upload()
        item = f"{self.base}notes.md/"
        self.client.delete(item)
        assert self.client.delete(item).status_code == 404
        assert self.client.get(f"{self.base}nope.md/").status_code == 404

    def test_can_upload_again_after_deleting_and_it_starts_unchecked(self):
        self.upload()
        item = f"{self.base}notes.md/"
        self.client.patch(item, {"enabled": True}, format="json")
        self.client.delete(item)

        assert self.upload().json()["enabled"] is False

    def test_delete_cannot_be_aimed_outside_the_skills_directory(self):
        keep = self.storage / "input" / self.sid / "file" / "keep.md"
        keep.parent.mkdir(parents=True)
        keep.write_text("x")
        session = ChatSession.objects.get(pk=self.sid)

        # 直接对业务层验证：不合格的名字一律当不存在，绝不会拼出 skills/ 以外的位置。
        # （不走 HTTP：带 .. 的路径在 URL 层会被 Django 当成 404 页面，测试客户端对它会崩，和这里要验证的无关。）
        for evil in ["..", "../file/keep.md", "..\\file\\keep.md", "a/../keep.md", ".", "state.json", ""]:
            with self.assertRaises(services.PluginError) as e:
                services.delete_plugin(session, evil)
            assert e.exception.status == 404, evil
        assert keep.exists()

    # --- 对接 core 的那一步：空方法，只验证它被调用 ---

    def test_core_hook_is_called_on_create_toggle_and_delete_with_the_session(self):
        with mock.patch("apps.services.apply_to_core") as hook:
            self.upload()
            self.client.patch(f"{self.base}notes.md/", {"enabled": True}, format="json")
            self.client.delete(f"{self.base}notes.md/")

        assert [c.args[0] for c in hook.call_args_list] == ["created", "updated", "deleted"]
        assert all(c.args[1].id == ChatSession.objects.get(pk=self.sid).id for c in hook.call_args_list)
        assert [c.args[2] for c in hook.call_args_list] == [
            {"name": "notes.md", "enabled": False},
            {"name": "notes.md", "enabled": True},
            {"name": "notes.md", "enabled": True},
        ]

    def test_core_hook_is_not_called_when_the_request_is_rejected(self):
        with mock.patch("apps.services.apply_to_core") as hook:
            self.upload(md("a.txt"))
            self.client.delete(f"{self.base}nope.md/")
        hook.assert_not_called()

    def test_core_hook_is_an_empty_method_that_does_nothing_and_returns_none(self):
        session = ChatSession.objects.get(pk=self.sid)
        assert services.apply_to_core("created", session, {"name": "x.md", "enabled": False}) is None

    def test_skills_dir_helper_sits_next_to_the_file_workspace(self):
        session = ChatSession.objects.get(pk=self.sid)
        assert Path(harness.skills_dir(session)) == self.skills_dir
        assert Path(harness.skills_dir(session)).parent == Path(harness.workspace_dir(session)).parent
