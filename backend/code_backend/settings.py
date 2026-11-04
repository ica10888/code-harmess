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
    "apps.apps.AppsConfig",
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
        "OPTIONS": {"timeout": 20},  # 遇到锁先等最多 20 秒，不立刻报 database is locked（WAL 在 apps.ready 里设置）
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
DEEPSEEK_MODEL = config.get("deepseek", "model", fallback="deepseek-flash")
DEEPSEEK_BASE_URL = config.get("deepseek", "base_url", fallback="https://api.deepseek.com")

# 存储路径：每个会话的文件放在 <HARNESS_STORAGE_DIR>/input/<会话id>/file/（读自 config.ini 的 [storage]）
HARNESS_STORAGE_DIR = str((BASE_DIR / config.get("storage", "dir", fallback="storage")).resolve())
# 默认插件目录：里面的 .md 对所有会话可见、不能删除（读自 config.ini 的 [storage] default_skills_dir）。
# 留空表示没有默认插件。相对路径按 backend/ 解析；目录不存在也不报错，只是没有默认插件。
_default_skills = config.get("storage", "default_skills_dir", fallback="").strip()
HARNESS_DEFAULT_SKILLS_DIR = str((BASE_DIR / _default_skills).resolve()) if _default_skills else ""
# 默认工作流目录：规则同默认插件（读自 config.ini 的 [storage] default_workflow_dir）
_default_workflow = config.get("storage", "default_workflow_dir", fallback="").strip()
HARNESS_DEFAULT_WORKFLOW_DIR = str((BASE_DIR / _default_workflow).resolve()) if _default_workflow else ""
# eureka 时刻的提示文本（读自 config.ini 的 [eureka] text）；留空时用 apps.workflows.DEFAULT_EUREKA_TEXT
EUREKA_TEXT = config.get("eureka", "text", fallback="").strip()
# eureka 之后展开工作流的插件（backend/eureka/plugins/<目录名>/，逗号分隔）；留空表示不展开任何工作流
# 文件变更提示的文本（config.ini 的 [eureka] file_change_text）；留空时用 apps.services.DEFAULT_FILE_CHANGE_TEXT
FILE_CHANGE_TEXT = config.get("eureka", "file_change_text", fallback="").strip()
EUREKA_PLUGINS = config.get("eureka", "plugins", fallback="").strip()
EUREKA_STAGE_TIMEOUT = config.getfloat("eureka", "stage_timeout_seconds", fallback=60.0)  # 单个阶段的超时（秒）
EUREKA_WEBHOOK_BASE_URL = config.get("eureka", "webhook_base_url", fallback="http://127.0.0.1:8000").strip()
# eureka 插件调用 LangChain 用的 DeepSeek 配置（config.ini 的 [eureka] llm_*）：是 [deepseek] 的一份拷贝，
# 留空的项回退到 [deepseek] 同名的值。插件不 import Django，所以由 apps 把这些值读出来、注入给插件（见 workflow_runtime）。
def _llm(key: str, fallback: str) -> str:
    return config.get("eureka", f"llm_{key}", fallback="").strip() or fallback


EUREKA_LLM = {
    "api_key": _llm("api_key", DEEPSEEK_API_KEY),
    "model": _llm("model", DEEPSEEK_MODEL),
    "base_url": _llm("base_url", DEEPSEEK_BASE_URL),
    "reasoning_effort": _llm("reasoning_effort", "low"),
    "timeout": float(_llm("timeout_seconds", "50")),
}
# deploy_game 插件：拷贝到哪里、对外用什么 ip / 域名访问（部署到服务器时改成服务器地址）
EUREKA_DEPLOY_DIR = (BASE_DIR / config.get("eureka", "deploy_dir", fallback="deployed").strip()).resolve()
EUREKA_DEPLOY_HOST = config.get("eureka", "deploy_host", fallback="127.0.0.1").strip() or "127.0.0.1"
EUREKA_DEPLOY_START_TIMEOUT = config.getfloat("eureka", "deploy_start_timeout_seconds", fallback=30.0)
EUREKA_MAX_LOG_LINES = 500  # 单个阶段最多保留的日志行数（和 eureka.webhook 的发送端上限一致）
# 单个上传文件的大小上限（字节）。Django 默认把超过 2.5MB 的请求体写临时文件、不会拒绝，所以我们自己检查
HARNESS_MAX_UPLOAD_BYTES = config.getint("storage", "max_upload_mb", fallback=20) * 1024 * 1024

# Harness：网页里没人能点「允许」，所以工作区版的 run_bash 不审批；其余需要审批的工具仍会被拒绝
HARNESS_TOOLS = ["read_file", "list_files", "edit_file", "run_bash"]
HARNESS_MAX_TOOL_ROUNDS = 20  # 一句话之后最多连续几轮工具调用
HARNESS_CONTEXT_BUDGET = 60_000  # 上一次请求的输入超过它，就把旧工具结果换成占位符
HARNESS_KEEP_TOOL_RESULTS = 5

# 身份由我们按 session cookie 自己判断，所以关掉 DRF 的认证（这样也不会触发 CSRF 检查）
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}
