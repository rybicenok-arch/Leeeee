"""🔒 Обязательная подписка на Telegram-канал + колбэк «Я подписался»."""
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message, TelegramObject)

log = logging.getLogger(__name__)

ALLOWED_STATUSES = ("member", "administrator", "creator")
ASK_COOLDOWN = 20.0      # сек между «подпишись»-сообщениями одному юзеру
OK_CACHE_TTL = 3600.0    # сек кэширования успешной проверки


async def check_membership(bot: Bot, channel_id: str, user_id: int) -> bool | None:
    """True = подписан, False = нет, None = проверка сломалась (fail-open)."""
    try:
        member = await bot.get_chat_member(channel_id, user_id)
    except Exception as exc:
        log.error("Не смог проверить подписку: %s\n"
                  "👉 Бот должен быть АДМИНОМ канала, иначе проверка невозможна!", exc)
        return None
    return member.status in ALLOWED_STATUSES


class SubscriptionMiddleware(BaseMiddleware):
    """Пускает к боту только подписчиков канала (если CHANNEL_ID задан)."""

    def __init__(self, bot: Bot, channel_id: str, channel_url: str):
        self.bot = bot
        self.channel_id = channel_id.strip()
        self.channel_url = channel_url.strip()
        self._ok_cache: dict[int, float] = {}
        self._last_ask: dict[int, float] = {}

    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        if not self.channel_id:  # проверка выключена
            return await handler(event, data)
        if not (isinstance(event, Message) and event.chat and event.chat.type == "private"):
            return await handler(event, data)
        user = event.from_user
        if user is None or user.is_bot:
            return await handler(event, data)

        cached = self._ok_cache.get(user.id)
        if cached and time.monotonic() - cached < OK_CACHE_TTL:
            return await handler(event, data)

        result = await check_membership(self.bot, self.channel_id, user.id)
        if result is not False:  # подписан или проверить не удалось
            if result:
                self._ok_cache[user.id] = time.monotonic()
            return await handler(event, data)

        await self._ask_subscribe(user.id)
        return None  # дальше цепочку не пускаем — план даже не создаём

    async def _ask_subscribe(self, user_id: int) -> None:
        if time.monotonic() - self._last_ask.get(user_id, 0.0) < ASK_COOLDOWN:
            return  # не спамим
        self._last_ask[user_id] = time.monotonic()

        rows = []
        if self.channel_url:
            rows.append([InlineKeyboardButton(text="📢 Подписаться на канал",
                                              url=self.channel_url)])
        rows.append([InlineKeyboardButton(text="✅ Я подписался — проверить",
                                          callback_data="sub:check")])
        text = ("🔒 <b>Сначала — подписка!</b>\n\n"
                "Я работаю только для подписчиков нашего канала 📢\n"
                "Подпишись и жми «Я подписался» — и погнали 🚀")
        try:
            await self.bot.send_message(
                user_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        except Exception:
            log.exception("Не смог отправить просьбу подписаться")


subscription_router = Router(name="subscription")


@subscription_router.callback_query(F.data == "sub:check")
async def on_sub_check(cb: CallbackQuery, bot: Bot, channel_id: str) -> None:
    if not channel_id:
        await cb.answer("✅ Всё ок!", show_alert=True)
        return
    result = await check_membership(bot, channel_id, cb.from_user.id)
    if result is not False:
        await cb.answer("🎉 Добро пожаловать! Пиши мне что-нибудь ✨", show_alert=True)
        if cb.message:
            try:
                await cb.message.edit_text(
                    "🎉 <b>Отлично, ты в деле!</b>\n\n"
                    "Напиши, например: «давай завтра погуляем в 17» 🚶")
            except Exception:
                pass
    else:
        await cb.answer("🚫 Пока не вижу тебя в канале. Подпишись и жми ещё раз 🙏",
                        show_alert=True)
