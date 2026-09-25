"""⚙️ Настройки: напоминания и часовой пояс. Сохраняются в сообщении с планом 🧭."""
import logging
import re
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.services.plan_service import DEFAULT_OFFSETS
from bot.services.plan_store import ChatState, PlanStore

log = logging.getLogger(__name__)

router = Router(name="settings")
router.message.filter(F.chat.type == "private")

OFFERS = (
    (120, "за 2 часа"),
    (60, "за час"),
    (30, "за 30 минут"),
    (15, "за 15 минут"),
    (0, "в момент события 🔥"),
)
LABELS = dict(OFFERS)

TZ_OPTIONS = (
    ("Europe/Kaliningrad", "UTC+2"), ("Europe/Moscow", "UTC+3"),
    ("Europe/Samara", "UTC+4"), ("Asia/Yekaterinburg", "UTC+5"),
    ("Asia/Omsk", "UTC+6"), ("Asia/Novosibirsk", "UTC+7"),
    ("Asia/Krasnoyarsk", "UTC+7"), ("Asia/Irkutsk", "UTC+8"),
    ("Asia/Yakutsk", "UTC+9"), ("Asia/Vladivostok", "UTC+10"),
    ("Asia/Magadan", "UTC+11"), ("Asia/Kamchatka", "UTC+12"),
)


def _tz_label(tz_name: str) -> str:
    for name, label in TZ_OPTIONS:
        if name == tz_name:
            return label
    return "кастомный"


def _offsets_human(offsets: set[int]) -> str:
    if not offsets:
        return "выключены 🔇"
    parts = []
    for o in sorted(offsets, reverse=True):
        if o == 0:
            parts.append("в момент 🔥")
        elif o >= 60 and o % 60 == 0:
            parts.append(f"за {o // 60} ч")
        else:
            parts.append(f"за {o} мин")
    return " · ".join(parts)


def settings_text(state: ChatState) -> str:
    return ("⚙️ <b>Настройки</b>\n\n"
            f"🔔 <b>Напоминания:</b> {_offsets_human(state.offsets)}\n"
            "Жми кнопки — включаю/выключаю на лету ✅\n\n"
            f"🌍 <b>Часовой пояс:</b> {state.tz_name} ({_tz_label(state.tz_name)})\n\n"
            "💾 Всё это хранится прямо в сообщении 🧭 — переживёт перезапуск!")


def settings_kb(state: ChatState) -> InlineKeyboardMarkup:
    rows = []
    for minutes, label in OFFERS:
        mark = "✅" if minutes in state.offsets else "❌"
        rows.append([InlineKeyboardButton(text=f"{mark} {label}",
                                          callback_data=f"notif:{minutes}")])
    if state.offsets:
        rows.append([InlineKeyboardButton(text="🔇 Выключить все", callback_data="notif:off")])
    else:
        rows.append([InlineKeyboardButton(text="🔔 Вернуть стандарт (час · 15 · момент)",
                                          callback_data="notif:reset")])
    rows.append([InlineKeyboardButton(text="🌍 Часовой пояс", callback_data="tz:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tz_text(state: ChatState) -> str:
    return (f"🌍 <b>Твой часовой пояс</b>\n\n"
            f"Сейчас: <b>{state.tz_name}</b> ({_tz_label(state.tz_name)})\n\n"
            "Выбирай — «сегодня/завтра» и все напоминания подстроются 👇")


def tz_kb(state: ChatState) -> InlineKeyboardMarkup:
    rows, row = [], []
    for name, label in TZ_OPTIONS:
        mark = "✅ " if name == state.tz_name else ""
        row.append(InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"tz:set:{name}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад к настройкам", callback_data="tz:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chat_id(cb: CallbackQuery) -> int:
    return cb.message.chat.id if cb.message and cb.message.chat else cb.from_user.id


@router.message(F.text.regexp(r"^/(?:settings|настройки|set)(?:@\w+)?(?:\s|$)", re.IGNORECASE))
async def cmd_settings(message: Message, plan_state: ChatState) -> None:
    await message.answer(settings_text(plan_state), reply_markup=settings_kb(plan_state))


# ---------- 🔔 напоминания ----------

@router.callback_query(F.data.startswith("notif:"))
async def toggle_notif(cb: CallbackQuery, store: PlanStore) -> None:
    action = cb.data.split(":", 1)[1]
    state = await store.get_state(_chat_id(cb))

    if action == "off":
        state.offsets = set()
        note = "🔇 Выключил все напоминания"
    elif action == "reset":
        state.offsets = set(DEFAULT_OFFSETS)
        note = "🔔 Вернул: за час · за 15 мин · в момент"
    else:
        try:
            minutes = int(action)
        except ValueError:
            await cb.answer()
            return
        if minutes in state.offsets:
            state.offsets.discard(minutes)
            note = f"❌ Убрал: {LABELS.get(minutes, minutes)}"
        else:
            state.offsets.add(minutes)
            note = f"✅ Включил: {LABELS.get(minutes, minutes)}"

    try:
        await store.sync(state)  # 💾 сохраняем в сообщение с планом
    except Exception:
        log.exception("Не смог сохранить настройки напоминаний")
    await cb.answer(note)
    if cb.message:
        try:
            await cb.message.edit_text(settings_text(state), reply_markup=settings_kb(state))
        except Exception:
            pass


# ---------- 🌍 часовой пояс ----------

@router.callback_query(F.data == "tz:menu")
async def tz_menu(cb: CallbackQuery, store: PlanStore) -> None:
    state = await store.get_state(_chat_id(cb))
    await cb.answer()
    if cb.message:
        try:
            await cb.message.edit_text(tz_text(state), reply_markup=tz_kb(state))
        except Exception:
            pass


@router.callback_query(F.data.startswith("tz:set:"))
async def tz_set(cb: CallbackQuery, store: PlanStore) -> None:
    name = cb.data.split("tz:set:", 1)[1]
    try:
        ZoneInfo(name)  # валидация
    except Exception:
        await cb.answer("🤔 Не знаю такой пояс", show_alert=True)
        return
    state = await store.get_state(_chat_id(cb))
    state.tz_name = name
    try:
        await store.sync(state)
    except Exception:
        log.exception("Не смог сохранить часовой пояс")
    await cb.answer(f"🌍 Готово: {name}")
    if cb.message:
        try:
            await cb.message.edit_text(settings_text(state), reply_markup=settings_kb(state))
        except Exception:
            pass


@router.callback_query(F.data == "tz:back")
async def tz_back(cb: CallbackQuery, store: PlanStore) -> None:
    state = await store.get_state(_chat_id(cb))
    await cb.answer()
    if cb.message:
        try:
            await cb.message.edit_text(settings_text(state), reply_markup=settings_kb(state))
        except Exception:
            pass
