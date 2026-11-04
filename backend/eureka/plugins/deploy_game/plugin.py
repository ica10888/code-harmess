"""示例插件：前端小游戏部署。和默认工作流 frontend-game-deploy 同名（工作流 frontmatter 的 name）。

三个阶段，每个阶段一个 reconcile 方法：
  copy    把会话工作区的文件拷贝到配置目录（<deploy_root>/port-<端口>/），先清掉旧内容
  start   在配置目录下用 npx 启动静态服务，等端口通；进程退出或超时就失败，日志（进程输出）原样回传给用户
  report  返回可访问的地址：http://<public_host>:<端口>；public_host 可配置，部署到服务器时填服务器 IP / 域名

安全：
  - 启动命令由本插件自己拼（npx serve），不执行模型给的 startCommand（它只是模型的文字，不是可信代码）；
  - 端口只认 32000-33000；目录名只由端口决定，没有任何路径成分。

幂等：同一个端口重跑 start 前，先结束上一次留下的进程（pid 记在 <deploy_root>/port-<端口>.pid），再启动。
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from eureka.base import EurekaPlugin, Stage, StageContext, StageResult

PORT_MIN, PORT_MAX = 32000, 33000
LOG_TAIL_CHARS = 1_500  # 失败时带进错误信息的日志长度


def npx_command(port: int, directory: Path | str) -> list[str]:
    """用 npx 启动静态服务：--yes 免去「是否安装」的交互提问；-l 指定端口。"""
    npx = shutil.which("npx") or "npx"  # Windows 上是 npx.cmd，要用完整路径才能不经 shell 启动
    return [npx, "--yes", "serve", str(directory), "-l", str(port)]


class DeployGame(EurekaPlugin):
    name = "frontend-game-deploy"

    def __init__(self) -> None:
        super().__init__()
        # 这些配置由主项目注入（插件不读 config.ini、不 import Django）；默认值只为单独使用和测试
        self.deploy_root: Path = Path("deployed")  # 拷贝到哪里
        self.public_host: str = "127.0.0.1"  # 对外访问用的 ip / 域名
        self.start_timeout: float = 30.0  # 等端口通的最长时间（秒）
        self.command_factory: Callable[[int, Path], list[str]] = npx_command  # 测试里换成本地的静态服务

    def stages(self) -> list[Stage]:
        return [
            Stage("copy", "拷贝文件到部署目录", "reconcile_copy"),
            Stage("start", "启动并检查日志", "reconcile_start"),
            Stage("report", "返回访问地址", "reconcile_report"),
        ]

    # --- 阶段 1：拷贝 -------------------------------------------------------------

    def reconcile_copy(self, ctx: StageContext) -> StageResult:
        port = _port(ctx)
        if ctx.workspace is None:
            raise ValueError("没有可用的会话工作区，无法拷贝文件")
        entry = str(ctx.payload.get("entry") or "index.html")
        if Path(entry).name != entry or not (Path(ctx.workspace) / entry).is_file():
            raise ValueError(f"工作区里没有入口文件 {entry}（只写文件名，不带目录）")
        target = self._target(port)
        if target.exists():
            shutil.rmtree(target)  # 先清掉再拷：重跑结果一样，不会混进旧文件
        shutil.copytree(ctx.workspace, target)
        ctx.log(f"已拷贝 {ctx.workspace} → {target}")
        return StageResult(output={"directory": str(target), "port": port})

    # --- 阶段 2：启动 -------------------------------------------------------------

    def reconcile_start(self, ctx: StageContext) -> StageResult:
        port = _port(ctx)
        directory = Path(ctx.previous.get("copy", {}).get("directory", ""))
        if not directory.is_dir():
            raise ValueError("缺少前一阶段 copy 的输出，请先完成该阶段")
        self._stop_previous(port)
        if _listening(port):
            raise RuntimeError(f"端口 {port} 已被占用，请换一个 32000-33000 之间的端口")

        command = self.command_factory(port, directory)
        log_file = self._log_path(port)
        ctx.log("启动命令：" + " ".join(command))
        with open(log_file, "wb") as sink:
            proc = subprocess.Popen(command, cwd=directory, stdout=sink, stderr=subprocess.STDOUT, **_detached())
        deadline = time.monotonic() + self.start_timeout
        while True:
            if proc.poll() is not None:
                tail = self._relay_log(ctx, log_file)
                raise RuntimeError(f"启动失败：进程已退出（退出码 {proc.returncode}）。日志：{tail}")
            if _listening(port):
                break
            if ctx.cancelled or time.monotonic() > deadline:
                _kill(proc.pid)
                tail = self._relay_log(ctx, log_file)
                raise RuntimeError(f"启动超时：{self.start_timeout:g} 秒内端口 {port} 没有响应。日志：{tail}")
            time.sleep(0.2)
        self._pid_path(port).write_text(str(proc.pid))
        self._relay_log(ctx, log_file)
        ctx.log(f"启动成功：端口 {port} 已在监听（pid {proc.pid}）")
        return StageResult(output={"started": True, "pid": proc.pid, "port": port})

    # --- 阶段 3：返回地址 ---------------------------------------------------------

    def reconcile_report(self, ctx: StageContext) -> StageResult:
        port = _port(ctx)
        url = f"http://{self.public_host}:{port}"
        ctx.log(f"访问地址：{url}")
        return StageResult(output={"url": url, "host": self.public_host, "port": port})

    # --- 内部 ---------------------------------------------------------------------

    def _target(self, port: int) -> Path:
        return Path(self.deploy_root) / f"port-{port}"

    def _log_path(self, port: int) -> Path:
        return Path(self.deploy_root) / f"port-{port}.log"  # 放在被服务的目录之外，日志不会被当成网页公开出去

    def _pid_path(self, port: int) -> Path:
        return Path(self.deploy_root) / f"port-{port}.pid"

    def _stop_previous(self, port: int) -> None:
        pid_file = self._pid_path(port)
        if pid_file.is_file():
            try:
                _kill(int(pid_file.read_text().strip()))
            except ValueError:
                pass
            pid_file.unlink(missing_ok=True)
            time.sleep(0.5)  # 给系统一点时间释放端口

    def _relay_log(self, ctx: StageContext, log_file: Path) -> str:
        """把进程输出逐行交给 ctx.log，返回末尾一段（用于错误信息）。"""
        text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else ""
        for line in text.splitlines()[-50:]:
            ctx.log(line)
        return text.strip()[-LOG_TAIL_CHARS:] or "（没有输出）"


def _port(ctx: StageContext) -> int:
    port = ctx.payload.get("port") if isinstance(ctx.payload, dict) else None
    if isinstance(port, bool) or not isinstance(port, int) or not PORT_MIN <= port <= PORT_MAX:
        raise ValueError(f"端口必须是 {PORT_MIN}-{PORT_MAX} 之间的整数：{port!r}")
    return port


def _listening(port: int) -> bool:
    for host in ("127.0.0.1", "::1"):  # serve 在新版 Node 上可能只监听 IPv6
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            continue
    return False


def _detached() -> dict:
    """让服务进程脱离当前进程组：插件线程结束或服务重载，游戏仍然在跑。"""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def _kill(pid: int) -> None:
    """结束进程（连同 npx 拉起的 node 子进程）；进程已经不在就当没事。"""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
        else:
            os.killpg(pid, signal.SIGTERM)
    except OSError:
        pass


PLUGIN = DeployGame  # 注册表按这个名字取插件类
