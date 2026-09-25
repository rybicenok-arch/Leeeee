"""⏰ Напоминания (у каждого юзера свои настройки) + фоновые задачи."""
import html
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.services import table_service
from bot.services.plan_store import PlanStore
from bot.utils import time_utils
from bot.utils.time_utils import RU_WD_SHORT, fmt_short

log = logging.getLogger(__name__)


class ReminderService:
    def __init__(self, bot: Bot, store: PlanStore):
        self.bot = bot
        self.store = store
        self._sent: set[str] = set()

    def start(self, scheduler: AsyncIOScheduler) -> None:
        scheduler.add_job(self.tick, "interval", seconds=60,
                          id="reminders", max_instances=1)
        scheduler.add_job(self.cleanup_job, "interval", minutes=30, id="cleanup")
        scheduler.add_job(self.monday_digest, "cron", day_of_week="mon", hour=8, id="digest")

    async def tick(self) -> None:
        """Каждую минуту сверяемся с настройками юзера и временем событий."""
        for state in list(self.store.states.values()):
            offsets = state.offsets or ()
            if not offsets:
                continue  # 🔇 напоминания выключены
            now = time_utils.now(state.tz)
            for event in list(state.plan.events):
                event_dt = datetime.combine(event.date, event.time, state.tz)
                for offset in offsets:
                    key = f"{state.chat_id}:{event.uid}:{offset}"
                    if key in self._sent:
                        continue
                    fire_at = event_dt - timedelta(minutes=offset)
                    if 0 <= (now - fire_at).total_seconds() < 90:
                        self._sent.add(key)
                        await self._remind(state.chat_id, event, event_dt, now, offset)

    async def _remind(self, chat_id: int, event, event_dt: datetime,
                      now: datetime, offset: int) -> None:
        title = html.escape(event.title)
        when = f"{RU_WD_SHORT[event.date.weekday()]} {fmt_short(event.date)} · {event.time:%H:%M}"
        try:
            if offset == 0:
                text = (f"🔥 <b>ПОРА!</b>\n\n{event.emoji} <b>{title}</b>\n🕗 {when}\n\n"
                        "Погнали! Удачи 💪")
            else:
                minutes = max(1, round((event_dt - now).total_seconds() / 60))
                text = f"⏰ <b>Через ~{minutes} мин</b>\n\n{event.emoji} <b>{title}</b>\n🕗 {when}"
            await self.bot.send_message(chat_id, text)
        except Exception:
            log.exception("Не отправилось напоминание")

    async def cleanup_job(self) -> None:
        for state in list(self.store.states.values()):
            if self.store.cleanup_past(state):
                try:
                    await self.store.sync(state)
                except Exception:
                    log.exception("cleanup: sync не удался")

    async def monday_digest(self) -> None:
        for state in list(self.store.states.values()):
            now = time_utils.now(state.tz)
            if not any(0 <= (e.date - now.date()).days <= 6 for e in state.plan.events):
                continue
            try:
                await self.bot.send_message(
                    state.chat_id,
                    "🌅 <b>Новая неделя, погнали!</b>\n\n"
                    + table_service.render_week(state.plan.events, now))
            except Exception:
                log.exception("Не отправился дайджест")
