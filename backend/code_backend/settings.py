"""Django 项目配置。服务器相关的值读自 backend/config.ini。"""

import configparser
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# backend/core 是独立的 Harness，里面的模块用顶层 import（import tools ...），所以把它的 src 加进 sys.path。
# 放在最后：不盖掉标准库和已安装的包。
sys.path.append(str(BASE_DIR / "core" / "src"))

config = configparser.ConfigParser()
config.read(BASE_DIR / "config.ini", encoding="utf-8")

SECRET_KEY = config.get("server", "secret_key")
DEBUG = config.getboolean("server", "debug")
ALLOWED_HOSTS = config.get("server", "allowed_hosts").split(",")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "corsheaders",
    "apps",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

ROOT_URLCONF = "code_backend.urls"
WSGI_APPLICATION = "code_backend.wsgi.application"
ASGI_APPLICATION = "code_backend.asgi.application"

# 开发期用 SQLite，免装数据库
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 用户身份 = Django session cookie（不用登录）
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30

CORS_ALLOWED_ORIGINS = config.get("cors", "allowed_origins").split(",")
CORS_ALLOW_CREDENTIALS = True

DEEPSEEK_API_KEY = config.get("deepseek", "api_key", fallback="")
DEEPSEEK_MODEL = config.get("deepseek", "model", fallback="deepseek-v4-flash")
DEEPSEEK_BASE_URL = config.get("deepseek", "base_url", fallback="https://api.deepseek.com")

# Harness：网页里没人能点「允许」，所以默认只给只读工具；run_bash 这类要审批的工具即使加进来也会被拒绝
HARNESS_TOOLS = ["read_file", "list_files"]
HARNESS_MAX_TOOL_ROUNDS = 20  # 一句话之后最多连续几轮工具调用
HARNESS_CONTEXT_BUDGET = 60_000  # 上一次请求的输入超过它，就把旧工具结果换成占位符
HARNESS_KEEP_TOOL_RESULTS = 5

# 身份由我们按 session cookie 自己判断，所以关掉 DRF 的认证（这样也不会触发 CSRF 检查）
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}
