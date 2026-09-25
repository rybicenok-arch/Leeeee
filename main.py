"""Точка входа: ИИ-планировщик без БД и volume. Запуск: python main.py"""
import asyncio
import logging

from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import load_config
from bot.handlers import commands, recovery, settings, start, text
from bot.middlewares.plan_middleware import PlanMiddleware
from bot.middlewares.subscription_middleware import SubscriptionMiddleware, subscription_router
from bot.services.groq_service import GroqService
from bot.services.plan_store import PlanStore
from bot.services.reminder_service import ReminderService

load_dotenv()


async def set_commands(bot: Bot) -> None:
    # Telegram разрешает в меню только латиницу — русские работают при вводе
    await bot.set_my_commands([
        BotCommand(command="plan", description="📅 Весь план"),
        BotCommand(command="today", description="☀️ План на сегодня"),
        BotCommand(command="tomorrow", description="🌤 План на завтра"),
        BotCommand(command="week", description="🗓 Ближайшая неделя"),
        BotCommand(command="settings", description="⚙️ Напоминания и пояс"),
        BotCommand(command="help", description="🆘 Помощь"),
    ])


async def main() -> None:
    config = load_config()
    logging.basicConfig(level=config.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.bot_token:
        raise SystemExit("❌ BOT_TOKEN не задан — см. .env.example")
    if not config.groq_api_key:
        logging.warning("⚠️ GROQ_API_KEY пуст — мозги работать не будут!")
    if not config.channel_id:
        logging.warning("⚠️ CHANNEL_ID пуст — проверка подписки ОТКЛЮЧЕНА")
    else:
        logging.info("📢 Обязательная подписка на канал: %s", config.channel_id)

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    store = PlanStore(bot, config.data_dir, config.tz)
    groq = GroqService(config.groq_api_key, config.groq_model)
    dp["store"] = store
    dp["groq"] = groq
    dp["tz"] = config.tz
    dp["channel_id"] = config.channel_id
    dp["channel_url"] = config.channel_url

    # 🔒 Подписка проверяется ПЕРВОЙ — раньше загрузки плана
    dp.message.outer_middleware(SubscriptionMiddleware(bot, config.channel_id, config.channel_url))
    dp.message.outer_middleware(PlanMiddleware(store))

    # порядок важен: колбэк подписки → start → recovery → settings → commands → text
    dp.include_router(subscription_router)
    dp.include_router(start.router)
    dp.include_router(recovery.router)
    dp.include_router(settings.router)
    dp.include_router(commands.router)
    dp.include_router(text.router)

    scheduler = AsyncIOScheduler(timezone=str(config.tz))
    ReminderService(bot, store).start(scheduler)
    scheduler.start()

    await set_commands(bot)
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот запущен")

    try:
        await dp.start_polling(bot)
    finally:
        await scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
