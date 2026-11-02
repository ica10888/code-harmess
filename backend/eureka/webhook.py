"""webhook 发送端：插件 ctx.log(text) 经它真实地 HTTP POST 回主项目；HTTP 不通就降级为直接写库，文字不丢。

  POST <base_url>/api/eureka/runs/<run_id>/stages/<stage_key>/logs/
  Authorization: Bearer <该次运行的令牌>
  {"seq": 3, "text": "已提取 5 条审查建议"}

为什么要降级：插件线程在 Django 进程里，向「自己所在的服务」发 HTTP。单线程的开发服务器会请求等自己而卡死；
所以请求超时很短，失败就改走注入的 fallback 直接写库。每行带 seq，接收端按 (stage, seq) 去重，所以重发是安全的。

不依赖 Django：HTTP 发送和降级写库都是注入的函数，测试里换成假的。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 2.0  # 秒：短，卡住就降级
RETRY_AFTER = 30.0  # 断路之后多久再试一次 HTTP；这期间的行直接降级写库，不再每行都等一遍超时
MAX_LINES_PER_STAGE = 500  # 单个阶段最多保留的日志行数（方案 §8）
TRUNCATED_NOTICE = "……（日志过多，后面的已省略）"


def _urllib_post(url: str, body: bytes, headers: dict, timeout: float) -> None:
    """默认的 HTTP 发送：真实的 POST。非 2xx 会抛 HTTPError，网络错误抛 URLError / OSError。"""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310  地址来自配置，不是用户输入
        response.read()


class Webhook:
    """一个阶段的 webhook：像函数一样调用 hook("文字")。"""

    def __init__(
        self,
        base_url: str,
        run_id: int,
        stage_key: str,
        token: str,
        http: Callable[[str, bytes, dict, float], None] = _urllib_post,
        fallback: Callable[[int, str, int, str], None] | None = None,
        max_lines: int = MAX_LINES_PER_STAGE,
        clock: Callable[[], float] = time.monotonic,
        retry_after: float = RETRY_AFTER,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/api/eureka/runs/{run_id}/stages/{stage_key}/logs/"
        self._run_id, self._key, self._token = run_id, stage_key, token
        self._http = http
        self._fallback = fallback
        self._max_lines = max_lines
        self._seq = 0
        self._lock = threading.Lock()
        # 断路器：HTTP 确认不通后，在 retry_after 秒内不再尝试，直接降级写库。
        # 不然服务不通时每一行都要等一遍（超时 × 重试）：20 行就是 80 秒，阶段早就超时了。
        self._clock = clock
        self._retry_after = retry_after
        self._open_until = 0.0

    def __call__(self, text: str) -> None:
        with self._lock:  # seq 的分配和发送放在一起：并发打印也不会重号、不会乱序
            if self._seq > self._max_lines:
                return  # 已经发过「省略」提示了
            self._seq += 1
            if self._seq > self._max_lines:
                text = TRUNCATED_NOTICE  # 第 max+1 行只发一次提示，之后的全部丢弃
            self._deliver(self._seq, text)

    def _deliver(self, seq: int, text: str) -> None:
        if self._clock() < self._open_until:
            self._save_directly(seq, text)  # 断路中：HTTP 不久前刚确认不通，不再白等
            return
        body = json.dumps({"seq": seq, "text": text}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"}
        for attempt in (1, 2):
            try:
                self._http(self._url, body, headers, HTTP_TIMEOUT)
                return
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    break  # 令牌不对：重试和以后的行都不会成功
            except ConnectionRefusedError:
                break  # 那边根本没有服务在听：重试同样会被拒绝，白等一次
            except (urllib.error.URLError, OSError) as e:
                reason = getattr(e, "reason", None)
                if isinstance(reason, ConnectionRefusedError):
                    break  # urllib 把连接被拒绝包成了 URLError(reason=ConnectionRefusedError)
                # 超时、连接被重置这类可能只是服务暂时忙：再试一次
        self._open_until = self._clock() + self._retry_after  # 两次都没送到：断路，一段时间内直接降级
        self._save_directly(seq, text)

    def _save_directly(self, seq: int, text: str) -> None:
        """HTTP 没送到：改为直接写库，这一行文字不丢。降级本身再失败，最坏就是丢这一行，绝不影响阶段。"""
        if self._fallback is None:
            return
        try:
            self._fallback(self._run_id, self._key, seq, text)
            logger.warning("eureka webhook 不通，已直接写库：run=%s stage=%s seq=%s", self._run_id, self._key, seq)
        except Exception:  # noqa: BLE001
            logger.exception("eureka webhook 降级写库也失败了：run=%s stage=%s seq=%s", self._run_id, self._key, seq)
