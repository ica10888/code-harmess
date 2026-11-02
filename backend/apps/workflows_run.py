"""工作流运行的辅助：webhook 令牌。

令牌由 SECRET_KEY 对 run_id 做 HMAC 派生，不存库：重启恢复后不用重新分发，接收端重新算一遍就能校验。
一个令牌只对它自己的那一次运行有效；比较用常量时间，避免通过响应时间猜令牌。
"""

import hashlib
import hmac

from django.conf import settings


def run_token(run_id: int) -> str:
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), f"eureka-run:{run_id}".encode(), hashlib.sha256).hexdigest()


def verify_run_token(run_id: int, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(run_token(run_id), token)
