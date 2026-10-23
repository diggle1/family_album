"""Storage failure codes and the human copy on the failure page."""

from __future__ import annotations

FAILURES: dict[str, dict[str, object]] = {
    "VOL_MISSING": {
        "title": "媒资卷没挂上",
        "reason": "约定路径（如 /data/media）不存在或不可写。",
        "steps": (
            "检查 compose 的 volumes 是否把数据卷挂到 /data。",
            "确认媒资目录存在，并且进程可以写入。",
            "改好后执行 docker compose up -d，再点下面的重试。",
        ),
    },
    "S3_UNREACHABLE": {
        "title": "对象存储连不上",
        "reason": "endpoint、网络或 DNS 不可达。",
        "steps": (
            "检查 .env 里的 S3_ENDPOINT（页面不显示具体值）。",
            "在部署这台机器上确认能访问对象存储。",
            "改好后执行 docker compose up -d，再点下面的重试。",
        ),
    },
    "S3_FORBIDDEN": {
        "title": "对象存储权限不对",
        "reason": "access key 或 bucket 策略拒绝读写。",
        "steps": (
            "核对 bucket 名和访问密钥的读写权限（页面只显示键名）。",
            "对照 README 的「S3 兼容」一节。",
            "改好后执行 docker compose up -d，再点下面的重试。",
        ),
    },
    "DB_DOWN": {
        "title": "应用数据起不来",
        "reason": "SQLite 或数据目录不可写。",
        "steps": (
            "检查数据卷权限和 DATABASE_URL。",
            "确认 /data 已挂载，并且 SQLite 文件可以创建。",
            "改好后执行 docker compose up -d，再点下面的重试。",
        ),
    },
    "APP_BOOT": {
        "title": "服务还没起来",
        "reason": "容器未就绪，或存储配置无法识别。",
        "steps": (
            "运行 docker compose ps 看容器是否在跑。",
            "运行 docker compose logs 看启动日志（日志里不写密钥）。",
            "改好后执行 docker compose up -d，再点下面的重试。",
        ),
    },
}

# Shown as key names only. Values stay masked; never pass live secrets into the template.
MASKED_KEYS = (
    "STORAGE_BACKEND",
    "MEDIA_ROOT",
    "S3_ENDPOINT",
    "S3_BUCKET",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
)


class StorageError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code if code in FAILURES else "APP_BOOT"
        self.message = message
        super().__init__(message)


def failure_copy(code: str) -> dict[str, object]:
    key = code if code in FAILURES else "APP_BOOT"
    item = FAILURES[key]
    return {
        "code": key,
        "title": item["title"],
        "reason": item["reason"],
        "steps": item["steps"],
    }
