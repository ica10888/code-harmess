"""SQLite 的并发设置：让后台线程写库和请求线程读写库同时进行时不互相卡死。

eureka 工作流的 runner 是一个常驻的后台线程，它要写阶段状态和日志；同时聊天请求也在读写同一个库。
SQLite 默认的日志模式（delete）下，写会锁住整个库，另一个连接就报 database is locked。
WAL 模式允许一个写者和多个读者同时进行；再加上「遇到锁先等一会儿」，就不会立刻报错。

Django 4.2 的 SQLite 后端没有 init_command 选项，所以用 connection_created 信号：每个新连接建立时执行一次。
"""

BUSY_TIMEOUT_SECONDS = 20

# Django 的 OPTIONS：timeout 是 sqlite3 连接等锁的时间（秒）
DATABASE_OPTIONS = {"timeout": BUSY_TIMEOUT_SECONDS}


def apply_pragmas(connection) -> None:
    """对一个连接（sqlite3 的或 Django 的都行）设置 WAL 和等待超时。"""
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_SECONDS * 1000}")
    connection.execute("PRAGMA synchronous=NORMAL")  # WAL 下推荐：断电最多丢最后一次提交，不会损坏


def on_connection_created(sender, connection, **kwargs) -> None:
    if connection.vendor == "sqlite":
        apply_pragmas(connection.connection)


def install() -> None:
    """在 AppConfig.ready() 里调用一次：之后每个新的 SQLite 连接都会自动设置。"""
    from django.db.backends.signals import connection_created

    connection_created.connect(on_connection_created, dispatch_uid="code_backend.sqlite.pragmas")
