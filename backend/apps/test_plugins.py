"""插件管理接口测试：每个会话一份，原样存在 <存储路径>/input/<会话id>/skills/<文件名>。

增删查都落在文件上，没有数据库表。没有「勾选」：用哪个插件由 core 的 skill_selector 在每句话之前选。
上传的要求：文件名以 .md 结尾，并且开头有 frontmatter（name + description，core 要用）；这部分由 test_inject.py 专门测。
这里的测试用的都是合规的内容（见 md()）。选中后把全文注入对话在 test_inject.py。
"""

import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps import harness, services
from apps.models import ChatSession


DEFAULT_BODY = "---\nname: demo\ndescription: 一个演示用的插件\n---\n\n# 随便写点什么\n"


def valid(body="正文"):
    """合规的插件内容（有 frontmatter）+ 任意正文，bytes。上传要求有 frontmatter，所以测试里的内容都得是它。"""
    head = "---\nname: demo\ndescription: 一个演示用的插件\n---\n"
    return head.encode("utf-8") + (body if isinstance(body, bytes) else body.encode("utf-8"))


def md(name="notes.md", content=DEFAULT_BODY):
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    return SimpleUploadedFile(name, data)


class PluginApiTests(TestCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.storage = Path(storage.name)
        # HARNESS_DEFAULT_SKILLS_DIR 必须显式关掉：否则这些测试会读到真实 config.ini 里配置的默认插件，
        # 测试结果就和机器上的配置文件有关了。默认插件的行为由下面的 DefaultPluginTests 专门测。
        override = override_settings(
            HARNESS_STORAGE_DIR=storage.name, HARNESS_MAX_UPLOAD_BYTES=1024, HARNESS_DEFAULT_SKILLS_DIR=""
        )
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
        assert res.json() == {"name": "notes.md", "default": False}
        assert (self.skills_dir / "notes.md").read_bytes() == DEFAULT_BODY.encode("utf-8")

    def test_content_must_have_frontmatter_but_nothing_else_about_it_is_checked(self):
        # 没有 frontmatter 的一律 400（详细的校验用例在 test_inject.py）；有 frontmatter 的，正文写什么都行
        assert self.upload(md("plain.md", "just text")).status_code == 400
        assert self.upload(md("empty.md", "")).status_code == 400
        body = "---\nname: ok\ndescription: d\n---\n" + "任意内容 ✓\n" * 3
        assert self.upload(md("fine.md", body)).status_code == 201

    def test_the_file_is_stored_byte_for_byte(self):
        body = valid("中文 ✓\r\n第二行\r\n")
        self.upload(md("a.md", body))
        assert (self.skills_dir / "a.md").read_bytes() == body


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
            fake = mock.Mock(read=lambda: valid("x"), size=1)
            fake.name = evil  # Mock 的 name 参数是保留的，只能赋值
            plugin = services.create_plugin(session, fake)
            assert plugin["name"] == "evil.md", evil
            services.delete_plugin(session, "evil.md")
        assert not (self.storage / "evil.md").exists() and not (self.storage / "input" / "evil.md").exists()

    def test_same_name_in_the_same_session_is_a_409_and_the_file_is_not_overwritten(self):
        self.upload(md("a.md", valid("第一版")))

        res = self.upload(md("a.md", valid("第二版")))

        assert res.status_code == 409 and "a.md" in res.json()["detail"]
        assert (self.skills_dir / "a.md").read_bytes() == valid("第一版")

    def test_size_limit_is_the_shared_upload_limit(self):
        head = "---\nname: big\ndescription: d\n---\n".encode("utf-8")
        assert self.upload(md("ok.md", head + b"x" * (1024 - len(head)))).status_code == 201  # 刚好等于上限
        assert self.upload(md("big.md", head + b"x" * (1025 - len(head)))).status_code == 413
        assert not (self.skills_dir / "big.md").exists()

    def test_missing_file_field_is_a_400(self):
        assert self.client.post(self.base, {}, format="multipart").status_code == 400

    # --- 查 ---

    def test_list_returns_the_sessions_plugins_ordered_by_name(self):
        for name in ["zeta.md", "alpha.md", "mid.md"]:
            self.upload(md(name))

        res = self.client.get(self.base)

        assert res.status_code == 200
        assert [p["name"] for p in res.json()] == ["alpha.md", "mid.md", "zeta.md"]
        assert set(res.json()[0]) == {"name", "default"}  # 列表里不带正文

    def test_empty_session_has_an_empty_list_and_listing_does_not_create_the_directory(self):
        assert self.client.get(self.base).json() == []
        assert not self.skills_dir.exists()

    def test_detail_returns_the_full_content_even_for_non_utf8_files(self):
        self.upload(md("a.md", valid("全文内容")))
        self.upload(md("bin.md", valid(b"ok\xff\xfeok")))

        assert self.client.get(f"{self.base}a.md/").json()["content"].endswith("全文内容")
        res = self.client.get(f"{self.base}bin.md/")
        assert res.status_code == 200 and "ok" in res.json()["content"]  # 解不了的字节用 � 代替，不崩

    def test_detail_works_for_names_that_need_url_encoding(self):
        self.upload(md("我的 笔记.md", valid("x")))
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
        assert other.delete(item).status_code == 404
        assert (self.skills_dir / "notes.md").exists()

    def test_unknown_session_is_a_404(self):
        assert self.client.get("/api/sessions/00000000-0000-0000-0000-000000000000/plugins/").status_code == 404

    # --- 删 ---

    def test_delete_removes_the_file(self):
        self.upload()
        item = f"{self.base}notes.md/"

        assert self.client.delete(item).status_code == 204

        assert not (self.skills_dir / "notes.md").exists()
        assert self.client.get(self.base).json() == []

    def test_deleting_twice_or_an_unknown_name_is_a_404(self):
        self.upload()
        item = f"{self.base}notes.md/"
        self.client.delete(item)
        assert self.client.delete(item).status_code == 404
        assert self.client.get(f"{self.base}nope.md/").status_code == 404


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


    def test_skills_dir_helper_sits_next_to_the_file_workspace(self):
        session = ChatSession.objects.get(pk=self.sid)
        assert Path(harness.skills_dir(session)) == self.skills_dir
        assert Path(harness.skills_dir(session)).parent == Path(harness.workspace_dir(session)).parent


class DefaultPluginTests(TestCase):
    """默认插件：config.ini 的 [storage] default_skills_dir 指向的目录。里面的 .md 对所有会话可见，不能删除，
    也不能被同名上传盖住。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.storage = Path(tmp.name) / "storage"
        self.defaults = Path(tmp.name) / "default_skills"
        self.defaults.mkdir()
        # write_bytes：Windows 上 write_text 会把 \n 改写成 \r\n，内容就和断言对不上了
        (self.defaults / "format.md").write_bytes("# 默认格式\n".encode("utf-8"))
        (self.defaults / "review.md").write_bytes("# 默认评审\n".encode("utf-8"))
        override = override_settings(
            HARNESS_STORAGE_DIR=str(self.storage),
            HARNESS_DEFAULT_SKILLS_DIR=str(self.defaults),
            HARNESS_MAX_UPLOAD_BYTES=1024,
        )
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.sid = self.new_session(self.client)
        self.base = f"/api/sessions/{self.sid}/plugins/"

    def new_session(self, client):
        return client.post("/api/sessions/", {}, format="json").json()["id"]

    def upload(self, name="mine.md", sid=None):
        return self.client.post(f"/api/sessions/{sid or self.sid}/plugins/", {"file": md(name)}, format="multipart")

    # --- 所有会话都能看到 ---

    def test_default_plugins_are_listed_in_every_session_and_marked_as_default(self):
        other = self.new_session(self.client)
        for sid in (self.sid, other):
            listing = self.client.get(f"/api/sessions/{sid}/plugins/").json()
            assert [(p["name"], p["default"]) for p in listing] == [("format.md", True), ("review.md", True)]

    def test_a_brand_new_session_with_no_directory_still_shows_them(self):
        assert not (self.storage / "input" / self.sid).exists()
        assert len(self.client.get(self.base).json()) == 2

    def test_defaults_come_first_then_the_sessions_own_plugins_each_sorted_by_name(self):
        self.upload("zeta.md")
        self.upload("alpha.md")

        names = [(p["name"], p["default"]) for p in self.client.get(self.base).json()]

        assert names == [("format.md", True), ("review.md", True), ("alpha.md", False), ("zeta.md", False)]

    def test_session_plugins_are_marked_not_default(self):
        assert self.upload().json()["default"] is False

    def test_adding_a_file_to_the_default_directory_shows_up_without_a_restart(self):
        (self.defaults / "later.md").write_text("x", encoding="utf-8")
        assert "later.md" in [p["name"] for p in self.client.get(self.base).json()]

    def test_only_md_files_in_the_default_directory_count(self):
        (self.defaults / "notes.txt").write_text("x")
        (self.defaults / "sub").mkdir()
        (self.defaults / "sub" / "inner.md").write_text("x")
        (self.defaults / "folder.md").mkdir()
        assert [p["name"] for p in self.client.get(self.base).json()] == ["format.md", "review.md"]

    def test_detail_of_a_default_plugin_returns_its_content(self):
        res = self.client.get(f"{self.base}format.md/")
        assert res.status_code == 200 and res.json()["content"] == "# 默认格式\n" and res.json()["default"] is True

    # --- 不能删除 ---

    def test_deleting_a_default_plugin_is_refused_with_403_and_the_file_stays(self):
        res = self.client.delete(f"{self.base}format.md/")

        assert res.status_code == 403
        assert "默认" in res.json()["detail"]
        assert (self.defaults / "format.md").read_bytes() == "# 默认格式\n".encode("utf-8")
        assert "format.md" in [p["name"] for p in self.client.get(self.base).json()]


    def test_deleting_from_one_session_never_affects_another(self):
        other = self.new_session(self.client)
        self.client.delete(f"{self.base}format.md/")
        assert "format.md" in [p["name"] for p in self.client.get(f"/api/sessions/{other}/plugins/").json()]

    # --- 同名上传 ---

    def test_uploading_a_file_with_a_default_plugins_name_is_a_409_and_writes_nothing(self):
        res = self.upload("format.md")

        assert res.status_code == 409 and "format.md" in res.json()["detail"]
        assert not (self.storage / "input" / self.sid / "skills" / "format.md").exists()
        assert (self.defaults / "format.md").read_bytes() == "# 默认格式\n".encode("utf-8")

    def test_the_name_clash_is_case_insensitive_like_the_file_system(self):
        assert self.upload("FORMAT.md").status_code == 409

    # --- 访问控制与路径安全 ---

    def test_other_browser_still_gets_404_for_a_session_it_does_not_own(self):
        other = APIClient()
        assert other.get(self.base).status_code == 404  # 默认插件也要先过会话归属这一关
        assert other.delete(f"{self.base}format.md/").status_code == 404

    def test_a_session_plugin_cannot_be_used_to_reach_into_the_default_directory(self):
        session = ChatSession.objects.get(pk=self.sid)
        for evil in ["..", "../default_skills/format.md", "..\\default_skills\\format.md", "state.json"]:
            with self.assertRaises(services.PluginError) as e:
                services.delete_plugin(session, evil)
            assert e.exception.status in (403, 404), evil
        assert (self.defaults / "format.md").exists()

    # --- 没配置 / 目录不存在 ---

    def test_no_default_directory_configured_means_no_default_plugins(self):
        with override_settings(HARNESS_DEFAULT_SKILLS_DIR=""):
            assert self.client.get(self.base).json() == []
            assert self.upload("format.md").status_code == 201  # 没有默认插件，就不存在同名冲突

    def test_a_default_directory_that_does_not_exist_is_silently_empty(self):
        with override_settings(HARNESS_DEFAULT_SKILLS_DIR=str(self.defaults / "nope")):
            assert self.client.get(self.base).json() == []
        assert not (self.defaults / "nope").exists()  # 读取不会把它建出来

    def test_session_plugin_still_works_next_to_defaults(self):
        self.upload("mine.md")
        assert self.client.delete(f"{self.base}mine.md/").status_code == 204
        assert [p["name"] for p in self.client.get(self.base).json()] == ["format.md", "review.md"]
