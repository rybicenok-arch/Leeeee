"""Конфигурация: всё берём из переменных окружения."""
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    bot_token: str
    groq_api_key: str
    groq_model: str
    channel_id: str       # 📢 обязательная подписка (пусто = выключена)
    channel_url: str      # ссылка на канал для кнопки
    data_dir: str
    tz: ZoneInfo
    log_level: str


def load_config() -> Config:
    tz_name = os.getenv("TZ", "Europe/Moscow")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Moscow")

    return Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        channel_id=os.getenv("CHANNEL_ID", "").strip(),
        channel_url=os.getenv("CHANNEL_URL", "").strip(),
        data_dir=os.getenv("DATA_DIR", "./data"),
        tz=tz,
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
