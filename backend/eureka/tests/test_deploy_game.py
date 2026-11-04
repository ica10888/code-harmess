import socket
import sys

import pytest

from eureka import registry
from eureka.base import StageContext, validate_stages
from eureka.plugins.deploy_game.plugin import PLUGIN, DeployGame


def free_port() -> int:
    """在插件允许的 32000-33000 里找一个空闲端口。"""
    for port in range(32500, 33000):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("32500-33000 里没有空闲端口")


def http_server(port: int, directory) -> list[str]:
    """测试里用 Python 自带的静态服务代替 npx：不联网、不装包。"""
    return [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(directory)]


def make_plugin(tmp_path, command=http_server, host="203.0.113.7"):
    plugin = DeployGame()
    plugin.deploy_root = tmp_path / "deployed"
    plugin.public_host = host
    plugin.start_timeout = 10.0
    plugin.command_factory = command
    return plugin


def ctx_for(tmp_path, port, previous=None):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    (workspace / "index.html").write_text("<h1>game</h1>", encoding="utf-8")
    lines = []
    payload = {"entry": "index.html", "port": port, "startCommand": f"npx serve . -l {port}", "url": "x", "desc": "d"}
    ctx = StageContext(
        run_id=1, session_id="sess", payload=payload, workspace=workspace, log=lines.append, previous=previous or {}
    )  # fmt: skip
    return ctx, lines


def stop(output):
    import os
    import signal

    try:
        os.kill(output["pid"], signal.SIGTERM)
    except OSError:
        pass


def test_name_and_stages():
    assert DeployGame().name == "frontend-game-deploy"
    assert PLUGIN is DeployGame
    assert [s.key for s in validate_stages(DeployGame())] == ["copy", "start", "report"]


def test_registry_loads_it():
    assert registry.load(["deploy_game"]).warnings == []


def test_copy_replaces_the_target_directory(tmp_path):
    plugin, port = make_plugin(tmp_path), free_port()
    target = plugin.deploy_root / f"port-{port}"
    target.mkdir(parents=True)
    (target / "stale.txt").write_text("old")
    ctx, _ = ctx_for(tmp_path, port)
    out = plugin.reconcile_copy(ctx).output
    assert (target / "index.html").read_text(encoding="utf-8") == "<h1>game</h1>"
    assert not (target / "stale.txt").exists()
    assert out["directory"] == str(target)


def test_copy_rejects_a_port_outside_the_range(tmp_path):
    ctx, _ = ctx_for(tmp_path, 80)
    with pytest.raises(ValueError, match="32000"):
        make_plugin(tmp_path).reconcile_copy(ctx)


def test_copy_needs_the_entry_file(tmp_path):
    ctx, _ = ctx_for(tmp_path, 32100)
    (ctx.workspace / "index.html").unlink()
    with pytest.raises(ValueError, match="index.html"):
        make_plugin(tmp_path).reconcile_copy(ctx)


def test_start_succeeds_when_the_port_answers_and_returns_the_log(tmp_path):
    plugin, port = make_plugin(tmp_path), free_port()
    copied = plugin.reconcile_copy(ctx_for(tmp_path, port)[0]).output
    ctx, lines = ctx_for(tmp_path, port, {"copy": copied})
    out = plugin.reconcile_start(ctx).output
    try:
        assert out["started"] is True
        assert isinstance(out["pid"], int)
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
        assert any("启动成功" in line for line in lines)
    finally:
        stop(out)


def test_start_fails_with_the_log_when_the_process_exits(tmp_path):
    bad = lambda port, directory: [sys.executable, "-c", "print('boom: cannot start'); raise SystemExit(1)"]
    plugin, port = make_plugin(tmp_path, bad), free_port()
    copied = plugin.reconcile_copy(ctx_for(tmp_path, port)[0]).output
    ctx, lines = ctx_for(tmp_path, port, {"copy": copied})
    with pytest.raises(RuntimeError, match="boom: cannot start"):
        plugin.reconcile_start(ctx)
    assert any("boom: cannot start" in line for line in lines)


def test_start_fails_when_the_port_is_already_taken(tmp_path):
    plugin = make_plugin(tmp_path)
    with socket.socket() as s:
        port = free_port()
        s.bind(("127.0.0.1", port))
        s.listen()
        copied = plugin.reconcile_copy(ctx_for(tmp_path, port)[0]).output
        with pytest.raises(RuntimeError, match="已被占用"):
            plugin.reconcile_start(ctx_for(tmp_path, port, {"copy": copied})[0])


def test_start_times_out_and_kills_a_process_that_never_listens(tmp_path):
    sleeper = lambda port, directory: [sys.executable, "-c", "import time; time.sleep(60)"]
    plugin, port = make_plugin(tmp_path, sleeper), free_port()
    plugin.start_timeout = 1.0
    copied = plugin.reconcile_copy(ctx_for(tmp_path, port)[0]).output
    with pytest.raises(RuntimeError, match="超时"):
        plugin.reconcile_start(ctx_for(tmp_path, port, {"copy": copied})[0])


def test_report_uses_the_configurable_public_host(tmp_path):
    plugin = make_plugin(tmp_path, host="203.0.113.7")
    ctx, lines = ctx_for(tmp_path, 32100, {"start": {"started": True, "pid": 1, "port": 32100}})
    out = plugin.reconcile_report(ctx).output
    assert out["url"] == "http://203.0.113.7:32100"
    assert any("http://203.0.113.7:32100" in line for line in lines)


def test_the_start_command_comes_from_the_plugin_not_from_the_models_text(tmp_path):
    from eureka.plugins.deploy_game.plugin import npx_command

    ctx, _ = ctx_for(tmp_path, 32100)
    ctx.payload["startCommand"] = "rm -rf /"
    assert "rm" not in npx_command(32100, tmp_path)
    assert npx_command(32100, tmp_path)[1:4] == ["--yes", "serve", str(tmp_path)]
