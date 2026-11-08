"""工作区图片/视频查看接口：格式白名单、Range（视频拖动进度条）、不能跑出工作区。"""

import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps import harness
from apps.models import ChatSession

DATA = bytes(range(100))


class WorkspaceMediaTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(HARNESS_STORAGE_DIR=tmp.name)
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()
        self.session = ChatSession.objects.create(owner_key=self.client.get("/api/sessions/").wsgi_request.session.session_key)
        root = Path(harness.workspace_dir(self.session))
        root.mkdir(parents=True)
        for name in ["a.PNG", "v.mp4", "m.mov", "x.svg", "p.webp", "s.mp3", "s.m4a"]:
            (root / name).write_bytes(DATA)
        (root / "a.txt").write_text("文本")
        (Path(tmp.name) / "secret.png").write_bytes(b"S")

    def url(self, tail=""):
        return f"/api/sessions/{self.session.id}/workspace/{tail}"

    def get(self, path, **headers):
        return self.client.get(self.url("media/"), {"path": path}, **headers)

    def body(self, response):
        return b"".join(response.streaming_content)

    def test_mainstream_formats_get_the_right_content_type(self):
        for path, ctype in [("a.PNG", "image/png"), ("v.mp4", "video/mp4"), ("m.mov", "video/quicktime"), ("x.svg", "image/svg+xml"), ("p.webp", "image/webp"), ("s.mp3", "audio/mpeg"), ("s.m4a", "audio/mp4")]:
            response = self.get(path)
            self.assertEqual((response.status_code, response["Content-Type"]), (200, ctype), path)
            self.assertEqual(self.body(response), DATA)
            self.assertEqual(response["Accept-Ranges"], "bytes")

    def test_range_requests_return_partial_content(self):
        response = self.get("v.mp4", HTTP_RANGE="bytes=10-19")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], "bytes 10-19/100")
        self.assertEqual(self.body(response), DATA[10:20])
        self.assertEqual(self.body(self.get("v.mp4", HTTP_RANGE="bytes=90-")), DATA[90:])
        self.assertEqual(self.body(self.get("v.mp4", HTTP_RANGE="bytes=-5")), DATA[-5:])

    def test_unsatisfiable_range_is_416(self):
        self.assertEqual(self.get("v.mp4", HTTP_RANGE="bytes=500-600").status_code, 416)

    def test_non_media_missing_and_escaping_paths_are_404(self):
        for p in ["a.txt", "nope.png", "../../../secret.png", ""]:
            self.assertEqual(self.get(p).status_code, 404, p)
