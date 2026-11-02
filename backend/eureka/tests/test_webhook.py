"""webhook 发送端：插件 ctx.log(text) → 真实 HTTP POST 回主项目；HTTP 不通就降级为直接写库，文字不丢（方案 §6）。

不依赖 Django：HTTP 和降级写库都通过注入的函数完成，这里用假的验证行为。
"""

import json
import threading
import urllib.error

import pytest

from eureka.webhook import Webhook


class FakeHttp:
    """记录每次 POST；可以让它失败或变慢。"""

    def __init__(self, fail_times=0, error=None):
        self.requests = []
        self.fail_times = fail_times
        self.error = error or urllib.error.URLError("connection refused")

    def __call__(self, url, body, headers, timeout):
        self.requests.append({"url": url, "body": json.loads(body), "headers": headers, "timeout": timeout})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.error


def make(http=None, fallback=None, **kw):
    http = http or FakeHttp()
    saved = []
    hook = Webhook(
        base_url="http://127.0.0.1:8000",
        run_id=5,
        stage_key="extract",
        token="tok-123",
        http=http,
        fallback=fallback or (lambda run_id, key, seq, text: saved.append((run_id, key, seq, text))),
        **kw,
    )
    return hook, http, saved


# --- 正常发送 ---


def test_a_line_is_posted_to_the_run_stage_logs_endpoint_with_the_token():
    hook, http, _ = make()

    hook("提取中")

    [req] = http.requests
    assert req["url"] == "http://127.0.0.1:8000/api/eureka/runs/5/stages/extract/logs/"
    assert req["headers"]["Authorization"] == "Bearer tok-123"
    assert req["headers"]["Content-Type"] == "application/json"
    assert req["body"] == {"seq": 1, "text": "提取中"}


def test_chinese_text_survives_the_round_trip_as_utf8():
    hook, http, _ = make()
    hook("中文 ✓ 😀")
    assert http.requests[0]["body"]["text"] == "中文 ✓ 😀"


def test_each_line_gets_the_next_seq_number():
    hook, http, _ = make()
    for text in ("一", "二", "三"):
        hook(text)
    assert [r["body"]["seq"] for r in http.requests] == [1, 2, 3]


def test_a_trailing_slash_in_the_base_url_does_not_double_up():
    http = FakeHttp()
    hook = Webhook("http://127.0.0.1:8000/", 5, "x", "t", http=http, fallback=lambda *a: None)
    hook("a")
    assert http.requests[0]["url"] == "http://127.0.0.1:8000/api/eureka/runs/5/stages/x/logs/"


def test_the_request_has_a_short_timeout_so_a_stuck_server_cannot_hang_the_stage():
    hook, http, _ = make()
    hook("a")
    assert 0 < http.requests[0]["timeout"] <= 3


def test_nothing_goes_to_the_fallback_when_http_works():
    hook, _, saved = make()
    hook("a")
    assert saved == []


# --- 降级：HTTP 不通，文字不丢 ---


def test_when_http_fails_the_line_is_written_directly_through_the_fallback_with_the_same_seq():
    hook, http, saved = make(FakeHttp(fail_times=99))

    hook("第一行")

    assert saved == [(5, "extract", 1, "第一行")]


def test_a_failed_send_is_retried_once_before_falling_back():
    hook, http, saved = make(FakeHttp(fail_times=1))

    hook("a")

    assert len(http.requests) == 2 and saved == []  # 第二次成功了，不用降级


def test_after_http_fails_twice_it_falls_back_and_the_next_line_also_goes_through_the_fallback_with_the_next_seq():
    # 断路器：HTTP 刚确认不通，冷却期内的下一行不再白等一遍超时，直接降级；序号仍然连续
    http = FakeHttp(fail_times=2)
    hook, _, saved = make(http)

    hook("a")  # 失败两次 → 断路，降级
    hook("b")  # 冷却期内：直接降级

    assert saved == [(5, "extract", 1, "a"), (5, "extract", 2, "b")]
    assert [r["body"]["seq"] for r in http.requests] == [1, 1]  # 第二行没有再去碰 HTTP


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError("refused"), TimeoutError("slow"), ConnectionResetError("reset"), OSError("net down")],
)
def test_every_kind_of_network_error_falls_back(error):
    hook, _, saved = make(FakeHttp(fail_times=99, error=error))
    hook("a")
    assert len(saved) == 1


def test_an_http_error_status_also_falls_back():
    err = urllib.error.HTTPError("u", 500, "server error", {}, None)
    hook, _, saved = make(FakeHttp(fail_times=99, error=err))
    hook("a")
    assert len(saved) == 1


def test_a_403_means_the_token_is_wrong_so_it_does_not_retry_but_still_saves_the_line():
    err = urllib.error.HTTPError("u", 403, "forbidden", {}, None)
    hook, http, saved = make(FakeHttp(fail_times=99, error=err))

    hook("a")

    assert len(http.requests) == 1 and len(saved) == 1  # 重试也不会成功，别浪费时间；文字照样落库


def test_if_the_fallback_itself_raises_it_never_breaks_the_stage():
    def boom(*a):
        raise RuntimeError("db down")

    hook, _, _ = make(FakeHttp(fail_times=99), fallback=boom)

    hook("a")  # 不抛：最坏就是丢这一行


# --- 限制 ---


def test_the_number_of_lines_per_stage_is_capped_and_the_overflow_is_announced_once():
    hook, http, _ = make(max_lines=3)

    for i in range(10):
        hook(f"行 {i}")

    texts = [r["body"]["text"] for r in http.requests]
    assert texts[:3] == ["行 0", "行 1", "行 2"]
    assert len(texts) == 4 and "已省略" in texts[3]  # 第 4 条是一次性的「后面的省略了」提示


def test_a_single_line_is_not_split_or_changed_by_the_webhook_itself():
    hook, http, _ = make()
    hook("a" * 1500)
    assert len(http.requests[0]["body"]["text"]) == 1500  # 截断是 StageContext.log 的事，这里原样发


# --- 线程安全 ---


def test_concurrent_prints_never_reuse_a_seq():
    hook, http, _ = make()

    threads = [threading.Thread(target=hook, args=(f"t{i}",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    seqs = sorted(r["body"]["seq"] for r in http.requests)
    assert seqs == list(range(1, 21))


# --- 默认的真实 HTTP 发送函数 ---


def test_the_default_sender_really_posts_json_over_http():
    import http.server

    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received["path"] = self.path
            received["auth"] = self.headers["Authorization"]
            received["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(201)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        saved = []
        hook = Webhook(
            f"http://127.0.0.1:{server.server_port}", 9, "report", "real-token",
            fallback=lambda *a: saved.append(a),
        )  # fmt: skip
        hook("真实请求")
        thread.join(5)
    finally:
        server.server_close()

    assert saved == []
    assert received["path"] == "/api/eureka/runs/9/stages/report/logs/"
    assert received["auth"] == "Bearer real-token" and received["body"] == {"seq": 1, "text": "真实请求"}


def test_the_default_sender_to_a_closed_port_falls_back_quickly():
    import socket
    import time

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # 拿到一个空闲端口，然后关掉：连上去一定被拒绝
    saved = []
    hook = Webhook(f"http://127.0.0.1:{port}", 1, "a", "t", fallback=lambda *a: saved.append(a))

    started = time.time()
    hook("x")

    assert time.time() - started < 6 and len(saved) == 1


# --- 断路：HTTP 已经确认不通，就别每一行都再等一遍超时 -----------------------------------


def test_once_http_is_known_to_be_down_the_following_lines_skip_it_and_go_straight_to_the_fallback():
    http = FakeHttp(fail_times=99)
    hook, _, saved = make(http)

    for text in ("一", "二", "三", "四"):
        hook(text)

    assert len(http.requests) == 2  # 只有第一行试了（含一次重试）；之后不再去碰 HTTP
    assert [s[2:] for s in saved] == [(1, "一"), (2, "二"), (3, "三"), (4, "四")]  # 文字一行不丢，序号连续


def test_a_connection_refused_error_trips_the_breaker_immediately_no_retry_wasted():
    # 连接被拒绝说明那边根本没有服务在听：重试同样会被拒绝，白等一次
    http = FakeHttp(fail_times=99, error=ConnectionRefusedError("refused"))
    hook, _, saved = make(http)

    hook("a")

    assert len(http.requests) == 1 and len(saved) == 1


def test_a_timeout_still_gets_one_retry_because_the_server_may_just_be_busy():
    http = FakeHttp(fail_times=1, error=TimeoutError("slow"))
    hook, _, saved = make(http)

    hook("a")

    assert len(http.requests) == 2 and saved == []  # 第二次成功了


def test_the_breaker_closes_again_after_a_cool_down_so_a_recovered_server_is_used_again():
    now = [1000.0]
    http = FakeHttp(fail_times=2)
    hook, _, saved = make(http, clock=lambda: now[0], retry_after=30)

    hook("a")  # 失败两次 → 断路，降级
    hook("b")  # 冷却期内：直接降级
    assert len(http.requests) == 2 and len(saved) == 2

    now[0] += 31  # 过了冷却期，服务也好了（FakeHttp 不再失败）
    hook("c")

    assert len(http.requests) == 3 and len(saved) == 2  # 重新试了 HTTP，并且成功了


def test_a_failed_probe_after_the_cool_down_trips_the_breaker_again():
    now = [0.0]
    http = FakeHttp(fail_times=99)
    hook, _, saved = make(http, clock=lambda: now[0], retry_after=30)

    hook("a")
    now[0] += 31
    hook("b")  # 重新试一次，又失败
    hook("c")  # 冷却期内：不再试

    assert len(http.requests) == 4 and len(saved) == 3  # a: 2 次，b: 2 次，c: 0 次


def test_a_403_also_trips_the_breaker_because_the_token_will_never_become_valid():
    err = urllib.error.HTTPError("u", 403, "forbidden", {}, None)
    http = FakeHttp(fail_times=99, error=err)
    hook, _, saved = make(http)

    hook("a")
    hook("b")

    assert len(http.requests) == 1 and len(saved) == 2


def test_each_stage_has_its_own_breaker_one_stages_failure_does_not_silence_another():
    http = FakeHttp(fail_times=2)
    saved = []
    fallback = lambda r, k, s, t: saved.append((k, s, t))  # noqa: E731
    first = Webhook("http://h", 1, "one", "t", http=http, fallback=fallback)
    second = Webhook("http://h", 1, "two", "t", http=http, fallback=fallback)

    first("a")  # 它的 HTTP 失败了
    second("b")  # 另一个阶段的 webhook 是独立的，HTTP 现在好了

    assert saved == [("one", 1, "a")] and http.requests[-1]["url"].endswith("/stages/two/logs/")


def test_the_total_time_for_many_lines_when_http_is_down_is_bounded_by_one_attempt_not_by_the_line_count():
    import socket
    import time

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    saved = []
    hook = Webhook(f"http://127.0.0.1:{port}", 1, "a", "t", fallback=lambda *a: saved.append(a))

    started = time.time()
    for i in range(20):
        hook(f"行{i}")

    assert time.time() - started < 6  # 以前是 20 × 4 秒 = 80 秒
    assert len(saved) == 20
