"""Хранение плана в сообщениях Telegram + настройки (напоминания/пояс) — там же."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from bot.services.plan_service import (DEFAULT_OFFSETS, Event, Plan, is_plan_text,
                                       parse_plan_text, parse_settings, render_plan)

log = logging.getLogger(__name__)


@dataclass
class ChatState:
    chat_id: int
    plan: Plan
    message_ids: list[int] = field(default_factory=list)  # сообщения-хранилища
    offsets: set[int] = field(default_factory=lambda: set(DEFAULT_OFFSETS))  # ⏰ мин
    tz_name: str = "Europe/Moscow"   # 🌍 пояс юзера
    fresh: bool = False  # только что создан

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.tz_name)
        except Exception:
            return ZoneInfo("Europe/Moscow")


class PlanStore:
    """Реестр состояний чатов + синхронизация плана с сообщениями."""

    def __init__(self, bot: Bot, data_dir: str, default_tz: ZoneInfo):
        self.bot = bot
        self.default_tz = default_tz
        self.default_tz_name = str(default_tz)
        self.states: dict[int, ChatState] = {}
        self.path = Path(data_dir) / "storage.json"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            log.warning("Не смог создать каталог данных: %s", self.path.parent)

    # ---------- storage.json (только message_id; volume НЕ нужен) ----------

    def _read_json(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_json(self) -> None:
        try:
            data = {"chats": {str(cid): {"message_ids": st.message_ids}
                              for cid, st in self.states.items()}}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            log.exception("Не смог записать storage.json")

    # ---------- доступ ----------

    async def get_state(self, chat_id: int) -> ChatState:
        state = self.states.get(chat_id)
        if state is not None:
            return state
        state = await self._recover(chat_id) or await self._create(chat_id)
        self.states[chat_id] = state
        return state

    def cleanup_past(self, state: ChatState) -> bool:
        """Убираем события старше «сегодня» по поясу юзера."""
        today = datetime.now(state.tz).date()
        kept = [e for e in state.plan.events if e.date >= today]
        if len(kept) != len(state.plan.events):
            state.plan.events = kept
            return True
        return False

    # ---------- создание плана с нуля ----------

    async def _create(self, chat_id: int) -> ChatState:
        now = datetime.now(self.default_tz)
        text = render_plan([], now, set(DEFAULT_OFFSETS), self.default_tz_name)[0]
        message = await self.bot.send_message(chat_id, text)
        await self._pin(chat_id, message.message_id)
        state = ChatState(chat_id=chat_id, plan=Plan(events=[], updated_at=now),
                          message_ids=[message.message_id],
                          offsets=set(DEFAULT_OFFSETS),
                          tz_name=self.default_tz_name, fresh=True)
        self._write_json()
        return state

    async def _pin(self, chat_id: int, message_id: int) -> None:
        try:
            await self.bot.pin_chat_message(chat_id, message_id, disable_notification=True)
        except Exception as exc:
            log.warning("Не смог закрепить сообщение: %s", exc)

    # ---------- восстановление ----------

    async def _recover(self, chat_id: int) -> ChatState | None:
        now = datetime.now(self.default_tz)
        events: list[Event] = []
        texts: list[str] = []
        message_ids: list[int] = []

        # Уровень 1: закреплённое сообщение — текст отдаёт get_chat бесплатно
        try:
            chat = await self.bot.get_chat(chat_id)
            pinned = getattr(chat, "pinned_message", None)
            if pinned is not None:
                pinned_text = getattr(pinned, "text", None)
                if pinned_text and is_plan_text(pinned_text):
                    texts.append(pinned_text)
                    events.extend(parse_plan_text(pinned_text))
                    message_ids.append(pinned.message_id)
        except Exception as exc:
            log.warning("get_chat(%s) не сработал: %s", chat_id, exc)

        # Уровень 2: storage.json (форвард → читаем → удаляем форвард)
        stored = self._read_json().get("chats", {}).get(str(chat_id), {})
        stored_ids = stored.get("message_ids", [])
        for message_id in stored_ids:
            if message_id in message_ids:
                continue
            text = await self._read_message(chat_id, message_id)
            if text and is_plan_text(text):
                texts.append(text)
                events.extend(parse_plan_text(text))
                message_ids.append(message_id)

        if not message_ids:
            return None

        # дедупликация + выбрасываем прошедшее
        seen, unique = set(), []
        for ev in events:
            if ev.uid not in seen:
                seen.add(ev.uid)
                unique.append(ev)
        unique = [e for e in unique if e.date >= now.date()]

        # ⚙️ настройки — из первого сообщения, где они есть
        offsets: set[int] | None = None
        tz_name: str | None = None
        for text in texts:
            parsed_offsets, parsed_tz = parse_settings(text)
            if offsets is None and parsed_offsets is not None:
                offsets = parsed_offsets
            if tz_name is None and parsed_tz:
                tz_name = parsed_tz

        return ChatState(
            chat_id=chat_id,
            plan=Plan(events=unique, updated_at=now),
            message_ids=stored_ids or message_ids,
            offsets=offsets if offsets is not None else set(DEFAULT_OFFSETS),
            tz_name=tz_name or self.default_tz_name,
            fresh=False)

    async def _read_message(self, chat_id: int, message_id: int) -> str | None:
        """Трюк: forward_message возвращает полный текст — читаем и удаляем форвард."""
        try:
            forwarded = await self.bot.forward_message(
                chat_id=chat_id, from_chat_id=chat_id, message_id=message_id)
            text = forwarded.text
            await self.bot.delete_message(chat_id, forwarded.message_id)
            return text
        except Exception as exc:
            log.warning("Не смог прочитать сообщение %s: %s", message_id, exc)
            return None

    # ---------- синхронизация: план + настройки → сообщения ----------

    async def sync(self, state: ChatState) -> None:
        self.cleanup_past(state)
        now = datetime.now(state.tz)
        state.plan.updated_at = now
        texts = render_plan(state.plan.events, now, state.offsets, state.tz_name)

        old_ids = list(state.message_ids)
        new_ids: list[int] = []
        replaced: list[int] = []

        for index, text in enumerate(texts):
            message_id = old_ids[index] if index < len(old_ids) else None
            if message_id is not None and await self._safe_edit(state.chat_id, message_id, text):
                new_ids.append(message_id)
                continue
            if message_id is not None:
                replaced.append(message_id)  # юзер удалил сообщение — пересоздадим
            message = await self.bot.send_message(state.chat_id, text)
            new_ids.append(message.message_id)

        for message_id in old_ids[len(texts):] + replaced:
            try:
                await self.bot.delete_message(state.chat_id, message_id)
            except Exception:
                pass

        state.message_ids = new_ids
        if not old_ids or new_ids[0] != old_ids[0]:
            await self._pin(state.chat_id, new_ids[0])
        self._write_json()

    async def _safe_edit(self, chat_id: int, message_id: int, text: str) -> bool:
        try:
            await self.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
            return True
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return True
            log.warning("Не отредактировать сообщение %s: %s", message_id, exc)
            return False
