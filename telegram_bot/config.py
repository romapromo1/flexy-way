from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _resolve_path(value: str, default: str) -> Path:
    path = Path(value or default)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _as_bool(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on", "да"}


@dataclass(frozen=True)
class Config:
    bot_token: str
    bot_username: str
    channel_id: str
    channel_url: str
    workbook_path: Path
    database_path: Path
    timezone: str
    token_ttl_hours: int
    polling_timeout: int
    manager_message: str
    data_secret_path: Path
    data_retention_days: int
    excel_pii_mode: str
    subscription_required: bool
    organizer_name: str
    privacy_contact: str
    rate_limit_per_minute: int
    prize_limit_exempt_username: str

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Config":
        file_values = _read_env_file(env_file or PROJECT_ROOT / ".env")

        def value(name: str, default: str = "") -> str:
            return os.environ.get(name, file_values.get(name, default)).strip()

        username = value("TELEGRAM_BOT_USERNAME").lstrip("@")
        return cls(
            bot_token=value("TELEGRAM_BOT_TOKEN"),
            bot_username=username,
            channel_id=value("TELEGRAM_CHANNEL_ID"),
            channel_url=value("TELEGRAM_CHANNEL_URL"),
            workbook_path=_resolve_path(
                value("PROMOCODES_XLSX"), "Flexy_Way_промокоды.xlsx"
            ),
            database_path=_resolve_path(
                value("BOT_DATABASE"), "telegram_bot/data/flexy_way_bot.sqlite3"
            ),
            timezone=value("BOT_TIMEZONE", "Europe/Moscow"),
            token_ttl_hours=int(value("TOKEN_TTL_HOURS", "24")),
            polling_timeout=int(value("POLLING_TIMEOUT_SECONDS", "30")),
            manager_message=value(
                "MANAGER_MESSAGE",
                "Менеджер свяжется с вами в ближайшее время!",
            ),
            data_secret_path=_resolve_path(
                value("BOT_DATA_SECRET"), "telegram_bot/data/local_secret.key"
            ),
            data_retention_days=int(value("DATA_RETENTION_DAYS", "30")),
            excel_pii_mode=value("EXCEL_PII_MODE", "full").casefold(),
            subscription_required=_as_bool(value("SUBSCRIPTION_REQUIRED", "true")),
            organizer_name=value("ORGANIZER_NAME", "Flexy Way"),
            privacy_contact=value("PRIVACY_CONTACT", "@flexy_way"),
            rate_limit_per_minute=int(value("RATE_LIMIT_PER_MINUTE", "20")),
            prize_limit_exempt_username=value("PRIZE_LIMIT_EXEMPT_USERNAME").lstrip("@"),
        )

    def validate_for_run(self) -> None:
        missing = []
        if not self.bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.channel_id:
            missing.append("TELEGRAM_CHANNEL_ID")
        if not self.channel_url:
            missing.append("TELEGRAM_CHANNEL_URL")
        if missing:
            raise ValueError("Не заполнены настройки: " + ", ".join(missing))
        if self.excel_pii_mode not in {"masked", "full"}:
            raise ValueError("EXCEL_PII_MODE должен быть masked или full")
        if not self.subscription_required:
            raise ValueError("Для этого проекта SUBSCRIPTION_REQUIRED должен быть true")
        if not 1 <= self.data_retention_days <= 365:
            raise ValueError("DATA_RETENTION_DAYS должен быть от 1 до 365")
        if not 1 <= self.token_ttl_hours <= 168:
            raise ValueError("TOKEN_TTL_HOURS должен быть от 1 до 168")
        if not 5 <= self.rate_limit_per_minute <= 120:
            raise ValueError("RATE_LIMIT_PER_MINUTE должен быть от 5 до 120")

    def validate_for_link(self) -> None:
        if not self.bot_username:
            raise ValueError("Не заполнена настройка TELEGRAM_BOT_USERNAME")
