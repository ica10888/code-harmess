import os
import sys

from django.apps import AppConfig


def is_server_process(argv: list[str], env: dict) -> bool:
    """这个进程是不是真正在提供服务的进程：只有它才该启动 eureka 的后台线程。

    manage.py 的 test / migrate / makemigrations / shell / check 等命令不能起线程，否则测试和迁移里会冒出一个后台线程。
    - runserver 默认带自动重载：先起一个父进程（没有 RUN_MAIN），它再起真正服务的子进程（RUN_MAIN=true），只在子进程里启动；
    - runserver --noreload 没有子进程，本进程就是服务；
    - gunicorn / uvicorn 等生产服务器没有 manage.py，用环境变量 EUREKA_RUNNER=1 显式打开。
    """
    if env.get("EUREKA_RUNNER") == "1":
        return True
    if len(argv) < 2 or os.path.basename(argv[0]) != "manage.py" or argv[1] != "runserver":
        return False
    return "--noreload" in argv or env.get("RUN_MAIN") == "true"


class AppsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps"

    def ready(self) -> None:
        from code_backend import sqlite

        sqlite.install()  # SQLite 开 WAL：后台线程写库不卡住请求线程

        if is_server_process(sys.argv, os.environ):
            from . import workflow_runtime

            workflow_runtime.start()  # 起后台线程，并把上次没做完的工作流运行重新入队
