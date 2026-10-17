import asyncio
import logging
import html
from typing import Union
from datetime import datetime, timedelta
from contextlib import suppress

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.exceptions import TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import database
import scheduler_solver
import wishes_db
from uk_calendar import get_uk_calendar, UkCalendarCallback

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")

ROLE_MAP = {
    "waiter": "Офіціант",
    "hostess": "Хостес",
    "loader": "Вантажник",
    "delivery_operator": "Оператор доставки",
    "admin": "Адміністратор"
}

ROLE_REVERSE_MAP = {
    "Офіціант": "waiter",
    "Хостес": "hostess",
    "Вантажник": "loader",
    "Оператор доставки": "delivery_operator"
}

pending_registrations = {}

async def send_long_message(chat_id: int, text: str, reply_markup=None):
    if len(text) <= 3800:
        await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode="HTML")
        return

    lines = text.split("\n")
    current_chunk = ""
    for line in lines:
        if len(current_chunk) + len(line) + 1 > 3800:
            await bot.send_message(chat_id, current_chunk, parse_mode="HTML")
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    if current_chunk.strip():
        await bot.send_message(chat_id, current_chunk, reply_markup=reply_markup, parse_mode="HTML")

class RegisterState(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_role = State()

class SwapState(StatesGroup):
    waiting_for_type = State()
    waiting_for_shift = State()
    waiting_for_colleague = State()

class GenerateState(StatesGroup):
    confirm_start = State()
    choose_start_date = State()
    choose_duration = State()
    confirm_overwrite = State()
    custom_req_mon_tue = State()
    custom_req_wed_thu = State()
    custom_req_fri_sun = State()
    confirm_final = State()

def get_guest_keyboard():
    kb = [[KeyboardButton(text="📝 Надіслати заявку на реєстрацію")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_phone_keyboard():
    kb = [[KeyboardButton(text="📱 Надіслати номер телефону", request_contact=True)]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_staff_keyboard():
    session = wishes_db.get_session()
    kb = []
    if session.get("is_active"):
        kb.append([KeyboardButton(text="✏️ Вказати побажання до графіку")])
    kb.extend([
        [KeyboardButton(text="📅 Мій графік на тиждень")],
        [KeyboardButton(text="🗓 Мій графік на весь період")],
        [KeyboardButton(text="🔄 Запропонувати заміну (Днем / Годинами)")]
    ])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_keyboard():
    session = wishes_db.get_session()
    kb = []
    if session.get("is_active"):
        kb.append([KeyboardButton(text="⏳ Стан збору побажань (Дострокова генерація)")])
    else:
        kb.append([KeyboardButton(text="⚙️ Розпочати збір побажань / Генерацію")])

    kb.extend([
        [KeyboardButton(text="👥 Список співробітників"), KeyboardButton(text="🔍 Графік на дату")],
        [KeyboardButton(text="📅 Графік усіх на тиждень"), KeyboardButton(text="🗓 Графік усіх на весь період")]
    ])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_roles_keyboard():
    kb = [
        [KeyboardButton(text="Офіціант"), KeyboardButton(text="Хостес")],
        [KeyboardButton(text="Оператор доставки"), KeyboardButton(text="Вантажник")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

async def send_daily_reminders():
    shifts = database.get_tomorrow_shifts()
    for s in shifts:
        user_id = s.get("telegram_id")
        if user_id and user_id != "0":
            role_title = ROLE_MAP.get(s.get('role'), s.get('role', ''))
            try:
                await bot.send_message(
                    chat_id=int(user_id),
                    text=f"🔔 <b>Нагадування про завтрашню зміну!</b>\n\n"
                         f"📅 Дата: <code>{html.escape(str(s.get('formatted_date', s['date'])))}</code>\n"
                         f"👤 Посада: <b>{html.escape(str(role_title))}</b>\n"
                         f"⏰ Час: <b>{html.escape(str(s['shift_hours']))}</b>\n\n"
                         f"Гарного робочого дня! 👍",
                    parse_mode="HTML"
                )
            except Exception as e:
                logging.error(f"Не вдалося надіслати сповіщення {user_id}: {e}")

async def notify_users_about_new_schedule(generated_shifts: list):
    user_shifts_map = {}
    for s in generated_shifts:
        user_id = str(s.get("telegram_id", "0"))
        if user_id != "0":
            if user_id not in user_shifts_map:
                user_shifts_map[user_id] = []
            user_shifts_map[user_id].append(s)

    for user_id_str, shifts in user_shifts_map.items():
        try:
            role_title = ROLE_MAP.get(shifts[0].get("role"), shifts[0].get("role", ""))
            msg = f"📢 <b>Опубліковано новий графік змін!</b>\n\n"
            msg += f"👤 Посада: <b>{html.escape(str(role_title))}</b>\n\n"
            msg += "Ваші заплановані зміни:\n"
            
            for s in shifts:
                msg += f"• <b>{html.escape(str(s['formatted_date']))}</b> — <code>{html.escape(str(s['shift_hours']))}</code>\n"
                
            msg += "\nПереглянути повний графік ви завжди можете в меню бота."
            
            await bot.send_message(chat_id=int(user_id_str), text=msg, parse_mode="HTML")
            await asyncio.sleep(0.05)
        except Exception as e:
            logging.error(f"Не вдалося надіслати сповіщення про новий графік користувачу {user_id_str}: {e}")

async def notify_all_about_wish_session(start_date_str: str, days_count: int):
    employees = database.get_all_employees()
    start_obj = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end_obj = start_obj + timedelta(days=days_count - 1)
    
    start_fmt = database.format_date_with_weekday(start_obj)
    end_fmt = database.format_date_with_weekday(end_obj)

    text = (
        f"📢 <b>УВАГА! РОЗПОЧАТО ЗБІР ПОБАЖАНЬ ДО ГРАФІКУ!</b>\n\n"
        f"Формується новий графік на період:\n"
        f"🗓 <b>з {html.escape(start_fmt)} по {html.escape(end_fmt)}</b> ({days_count} днів).\n\n"
        f"У вас є <b>24 години</b>, щоб подати обов'язкові вихідні та свої побажання через кнопку нижче:\n"
        f"👉 <b>«✏️ Вказати побажання до графіку»</b>"
    )

    for emp in employees:
        emp_id = emp.get("telegram_id")
        if emp.get("role") != "admin" and emp_id:
            try:
                await bot.send_message(chat_id=int(emp_id), text=text, reply_markup=get_staff_keyboard(), parse_mode="HTML")
                await asyncio.sleep(0.05)
            except Exception as e:
                logging.error(f"Не вдалося надіслати сповіщення про збір {emp_id}: {e}")

async def auto_clear_old_data_job():
    logging.info("🧹 Запуск щоденного очищення застарілого графіку та вихідних...")
    database.clear_old_schedules(days_offset=2)
    database.clear_old_wishes(days_offset=7)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    database.init_admin_if_not_exists()
    user_data = database.get_user_data(user_id)

    if not user_data:
        await message.answer(
            f"Вітаю, {message.from_user.first_name}!\n\n"
            "⚠️ Вас немає в системі співробітників.\n"
            "Натисніть кнопку нижче, щоб надіслати заявку на реєстрацію Адміністратору.",
            reply_markup=get_guest_keyboard()
        )
        return

    role = user_data.get("role")
    name = str(user_data.get("full_name", "")).replace("•", "").replace("ПІБ:", "").strip()

    if role == "admin":
        await message.answer(f"Вітаю, Адміністраторе {name}!", reply_markup=get_admin_keyboard())
    else:
        await message.answer(f"Вітаю, {name} ({ROLE_MAP.get(role, role)})!", reply_markup=get_staff_keyboard())

# --- МОДУЛЬ РЕЄСТРАЦІЇ НОВИХ СПІВРОБІТНИКІВ ---

@dp.message(F.text == "📝 Надіслати заявку на реєстрацію")
async def start_registration(message: types.Message, state: FSMContext):
    await state.set_state(RegisterState.waiting_for_name)
    await message.answer("Введіть ваше <b>Прізвище та Ім'я</b> (наприклад: <i>Петренко Іван</i>):", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")

@dp.message(RegisterState.waiting_for_name)
async def process_reg_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 3:
        await message.answer("Будь ласка, введіть коректні Прізвище та Ім'я.")
        return

    await state.update_data(full_name=name)
    await state.set_state(RegisterState.waiting_for_phone)
    await message.answer("Натисніть кнопку нижче, щоб надіслати свій номер телефону:", reply_markup=get_phone_keyboard())

@dp.message(RegisterState.waiting_for_phone, F.contact)
async def process_reg_phone_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    await state.update_data(phone=phone)
    await state.set_state(RegisterState.waiting_for_role)
    await message.answer("Оберіть вашу посаду:", reply_markup=get_roles_keyboard())

@dp.message(RegisterState.waiting_for_phone)
async def process_reg_phone_text(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    await state.update_data(phone=phone)
    await state.set_state(RegisterState.waiting_for_role)
    await message.answer("Оберіть вашу посаду:", reply_markup=get_roles_keyboard())

@dp.message(RegisterState.waiting_for_role, F.text.in_(list(ROLE_REVERSE_MAP.keys())))
async def process_reg_role(message: types.Message, state: FSMContext):
    role_code = ROLE_REVERSE_MAP[message.text]
    data = await state.get_data()
    
    full_name = str(data.get("full_name", ""))
    phone = str(data.get("phone", ""))
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else "немає"

    await state.clear()

    pending_registrations[str(user_id)] = {
        "full_name": full_name,
        "phone": phone,
        "role": role_code
    }

    await message.answer(
        "✅ <b>Заявку надіслано Адміністратору!</b>\n\nОчікуйте підтвердження реєстрації.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Схвалити реєстрацію", callback_data=f"app_reg_{user_id}"),
        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"rej_reg_{user_id}")
    ]])

    try:
        await bot.send_message(
            chat_id=config.ADMIN_TELEGRAM_ID,
            text=f"📝 <b>Нова заявка на реєстрацію!</b>\n\n"
                 f"👤 ПІБ: <b>{html.escape(full_name)}</b>\n"
                 f"📱 Телефон: <code>{html.escape(phone)}</code>\n"
                 f"💼 Посада: <b>{html.escape(message.text)}</b>\n"
                 f"🆔 Telegram ID: <code>{user_id}</code> ({html.escape(username)})",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не вдалося надіслати заявку адміну: {e}")

@dp.callback_query(F.data.startswith("app_reg_"))
async def approve_registration(callback: types.CallbackQuery):
    user_id_str = callback.data.replace("app_reg_", "")
    user_id = int(user_id_str)

    reg_info = pending_registrations.get(user_id_str, {})
    full_name = str(reg_info.get("full_name", "Співробітник"))
    phone = str(reg_info.get("phone", ""))
    role_code = reg_info.get("role", "waiter")

    database.add_employee(user_id, full_name, role_code, phone)
    pending_registrations.pop(user_id_str, None)
    
    await callback.message.edit_text(f"✅ Співробітника <b>{html.escape(full_name)}</b> успішно додано в систему!", parse_mode="HTML")
    
    try:
        await bot.send_message(
            chat_id=user_id,
            text="🎉 <b>Вашу заявку на реєстрацію підтверджено!</b>\n\nТепер ви можете користуватися всіма функціями бота.",
            reply_markup=get_staff_keyboard(),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не вдалося сповістити користувача {user_id}: {e}")

@dp.callback_query(F.data.startswith("rej_reg_"))
async def reject_registration(callback: types.CallbackQuery):
    user_id_str = callback.data.replace("rej_reg_", "")
    user_id = int(user_id_str)
    
    pending_registrations.pop(user_id_str, None)
    await callback.message.edit_text("❌ Заявку на реєстрацію відхилено.")
    
    try:
        await bot.send_message(chat_id=user_id, text="❌ Вашу заявку на реєстрацію було відхилено Адміністратором.")
    except Exception as e:
        logging.error(f"Не вдалося сповістити про відхилення {user_id}: {e}")

# --- МОДУЛЬ ПОБАЖАНЬ ДЛЯ СПІВРОБІТНИКІВ ---

@dp.message(F.text == "✏️ Вказати побажання до графіку")
async def start_user_wish_input(message: types.Message):
    session = wishes_db.get_session()
    if not session.get("is_active"):
        await message.answer("⚠️ Наразі немає активного збору побажань.", reply_markup=get_staff_keyboard())
        return

    user_id = message.from_user.id
    u_wishes = wishes_db.get_user_wishes(user_id)
    off_dates = u_wishes.get("off_dates", set())

    start_date_str = session["start_date"]
    days_count = session["days_count"]
    start_obj = datetime.strptime(start_date_str, "%Y-%m-%d").date()

    builder = []
    for i in range(days_count):
        cur_d = start_obj + timedelta(days=i)
        cur_d_str = cur_d.strftime("%Y-%m-%d")
        fmt_d = database.format_date_with_weekday(cur_d)
        
        status = "🔴 Вказано ВИХІДНИЙ" if cur_d_str in off_dates else "⚪️ Робочий можливий"
        builder.append([InlineKeyboardButton(
            text=f"{fmt_d} — {status}",
            callback_data=f"toggle_wish_date_{cur_d_str}"
        )])

    builder.append([InlineKeyboardButton(text="✅ Зберегти та завершити", callback_data="save_wish_done")])
    kb = InlineKeyboardMarkup(inline_keyboard=builder)

    await message.answer(
        "✏️ <b>Оберіть ваші обов'язкові ВИХІДНІ дні:</b>\n\n"
        "Натискайте на потрібні дати (можна обрати кілька довільних днів), після чого натисніть кнопку підтвердження нижче.",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("toggle_wish_date_"))
async def process_toggle_wish_date(callback: types.CallbackQuery):
    session = wishes_db.get_session()
    if not session.get("is_active"):
        await callback.answer("⚠️ Збір побажань вже закрито!", show_alert=True)
        return

    date_str = callback.data.replace("toggle_wish_date_", "")
    user_id = callback.from_user.id
    
    off_dates = wishes_db.toggle_off_date(user_id, date_str)

    start_date_str = session["start_date"]
    days_count = session["days_count"]
    start_obj = datetime.strptime(start_date_str, "%Y-%m-%d").date()

    builder = []
    for i in range(days_count):
        cur_d = start_obj + timedelta(days=i)
        cur_d_str = cur_d.strftime("%Y-%m-%d")
        fmt_d = database.format_date_with_weekday(cur_d)
        
        status = "🔴 Вказано ВИХІДНИЙ" if cur_d_str in off_dates else "⚪️ Робочий можливий"
        builder.append([InlineKeyboardButton(
            text=f"{fmt_d} — {status}",
            callback_data=f"toggle_wish_date_{cur_d_str}"
        )])

    builder.append([InlineKeyboardButton(text="✅ Зберегти та завершити", callback_data="save_wish_done")])
    kb = InlineKeyboardMarkup(inline_keyboard=builder)

    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "save_wish_done")
async def process_save_wish_done(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    u_wishes = wishes_db.get_user_wishes(user_id)
    off_dates = u_wishes.get("off_dates", set())

    user_data = database.get_user_data(user_id)
    clean_name = str(user_data.get("full_name", "")).replace("•", "").replace("ПІБ:", "").strip() if user_data else "Співробітник"
    user_role = user_data.get("role", "") if user_data else ""

    database.save_user_wish_off_dates(user_id, clean_name, user_role, off_dates)

    if off_dates:
        dates_fmt = ", ".join([datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m") for d in sorted(off_dates)])
        msg = f"✅ <b>Ваші вихідні збережено та внесено в Google Таблицю!</b>\n\nОбрано обов'язкові вихідні: <code>{dates_fmt}</code>.\nВони будуть враховані при генерації."
    else:
        msg = "✅ <b>Збережено!</b> Ви не обрали жодного обов'язкового вихідного."

    await callback.message.edit_text(msg, parse_mode="HTML")

# --- МОДУЛЬ АДМІНІСТРАТОРА ТА ЗБОРУ ПОБАЖАНЬ ---

@dp.message(F.text.in_({"⚙️ Розпочати збір побажань / Генерацію", "⚙️ Згенерувати графік"}))
async def admin_start_session_flow(message: types.Message, state: FSMContext):
    user_data = database.get_user_data(message.from_user.id)
    if not user_data or user_data.get("role") != "admin":
        return

    session = wishes_db.get_session()
    if session.get("is_active"):
        await admin_show_wish_status(message)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Так, розпочати", callback_data="gen_confirm_start"),
        InlineKeyboardButton(text="❌ Скасувати", callback_data="gen_cancel")
    ]])

    await state.set_state(GenerateState.confirm_start)
    await message.answer(
        "⚙️ <b>Формування нового графіку (Збір побажань / OR-Tools)</b>\n\n"
        "Ви бажаєте відкрити 24-годинну сесію збору вихідних і побажань від працівників?",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.message(F.text == "⏳ Стан збору побажань (Дострокова генерація)")
async def admin_show_wish_status(message: types.Message):
    session = wishes_db.get_session()
    if not session.get("is_active"):
        await message.answer("У даний момент немає активного збору побажань.", reply_markup=get_admin_keyboard())
        return

    all_wishes = wishes_db.get_all_wishes()
    employees = database.get_all_employees()
    staff_list = [e for e in employees if e.get("role") != "admin"]

    submitted_count = len([e_id for e_id in all_wishes if all_wishes[e_id].get("off_dates")])
    total_staff = len(staff_list)

    text = (
        f"⏳ <b>АКТИВНИЙ ЗБІР ПОБАЖАНЬ ДО ГРАФІКУ</b>\n\n"
        f"🗓 Період графіку: <b>з {session['start_date']} на {session['days_count']} днів</b>\n"
        f"📊 Подали вихідні: <b>{submitted_count} / {total_staff}</b> співробітників.\n\n"
        f"Ви можете зачекати завершення 24 годин або закрити збір достроково та згенерувати графік просто зараз."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚡️ Достроково закрити і ЗГЕНЕРУВАТИ", callback_data="admin_force_generate")
    ]])

    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "gen_confirm_start", GenerateState.confirm_start)
async def process_gen_start_calendar(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GenerateState.choose_start_date)
    await callback.message.edit_text(
        "📆 <b>Крок 1/2: Оберіть дату ПОЧАТКУ періоду в календарі (до 30 днів від сьогодні):</b>",
        reply_markup=get_uk_calendar(max_days_ahead=30, allow_past=False),
        parse_mode="HTML"
    )

@dp.callback_query(UkCalendarCallback.filter(), GenerateState.choose_start_date)
async def process_gen_calendar_choice(callback_query: types.CallbackQuery, callback_data: UkCalendarCallback, state: FSMContext):
    action = callback_data.action

    if action == "IGNORE":
        await callback_query.answer()
        return

    if action in ["PREV-MONTH", "NEXT-MONTH"]:
        with suppress(TelegramBadRequest):
            await callback_query.message.edit_reply_markup(
                reply_markup=get_uk_calendar(year=callback_data.year, month=callback_data.month, max_days_ahead=30, allow_past=False)
            )
        await callback_query.answer()
        return

    if action in ["DAY", "TODAY"]:
        today = datetime.now().date()
        start_date_obj = datetime(callback_data.year, callback_data.month, callback_data.day).date()
        
        if start_date_obj < today or start_date_obj > (today + timedelta(days=30)):
            await callback_query.answer("⚠️ Цю дату не можна обрати! Оберіть дату від сьогодні до +30 днів.", show_alert=True)
            return

        start_str = start_date_obj.strftime("%Y-%m-%d")
        formatted_start = database.format_date_with_weekday(start_date_obj)

        await state.update_data(start_date=start_str, formatted_start=formatted_start)
        await state.set_state(GenerateState.choose_duration)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗓 7 днів (1 тиждень)", callback_data="gen_dur_7")],
            [InlineKeyboardButton(text="🗓 14 днів (2 тижні)", callback_data="gen_dur_14")],
            [InlineKeyboardButton(text="🗓 30 днів (місяць)", callback_data="gen_dur_30")],
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="gen_cancel")]
        ])

        await callback_query.message.edit_text(
            f"✅ Обрано дату початку: <b>{html.escape(formatted_start)}</b>\n\n"
            f"⏱ <b>Крок 2/2: Оберіть тривалість періоду генерації (максимум 30 днів):</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )

@dp.callback_query(F.data.startswith("gen_dur_"), GenerateState.choose_duration)
async def process_gen_duration_choice(callback: types.CallbackQuery, state: FSMContext):
    days_count = int(callback.data.replace("gen_dur_", ""))
    
    if days_count > 30 or days_count <= 0:
        await callback.answer("⚠️ Максимальна тривалість періоду генерації — 30 днів!", show_alert=True)
        return

    data = await state.get_data()
    start_str = data["start_date"]

    await state.update_data(days_count=days_count)

    wishes_db.start_session(start_str, days_count)

    await callback.message.edit_text(
        f"🚀 <b>ЗБІР ПОБАЖАНЬ УСПІШНО РОЗПОЧАТО!</b>\n\n"
        f"🗓 Період: <b>з {start_str} на {days_count} днів</b>.\n"
        f"📩 Усім співробітникам надіслано сповіщення з відкриттям 24-годинного вікна.",
        parse_mode="HTML"
    )

    asyncio.create_task(notify_all_about_wish_session(start_str, days_count))

@dp.callback_query(F.data == "admin_force_generate")
async def process_admin_force_generate(callback: types.CallbackQuery, state: FSMContext):
    session = wishes_db.get_session()
    if not session.get("is_active"):
        await callback.answer("Сесія вже була закрита.", show_alert=True)
        return

    start_str = session["start_date"]
    days_count = session["days_count"]

    await state.update_data(start_date=start_str, days_count=days_count)
    wishes_db.stop_session()

    await callback.message.edit_text("⏳ <b>Збір закрито достроково. Запускаємо генератор OR-Tools...</b>", parse_mode="HTML")
    await ask_template_or_custom(callback.message, state)

async def ask_template_or_custom(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡️ Точні години за посадами (Офіціанти, Хостес...)", callback_data="gen_tmpl_default")],
        [InlineKeyboardButton(text="✏️ Налаштувати потреби вручну", callback_data="gen_tmpl_custom")],
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="gen_cancel")]
    ])
    await message.edit_text(
        "Оберіть спосіб задання потреб у персоналі:",
        reply_markup=kb
    )

@dp.callback_query(F.data == "gen_tmpl_default")
async def process_default_template(callback: types.CallbackQuery, state: FSMContext):
    reqs = {
        0: {"waiter": 6,  "loader": 1, "delivery_operator": 1, "hostess": 2},
        1: {"waiter": 6,  "loader": 1, "delivery_operator": 1, "hostess": 2},
        2: {"waiter": 8,  "loader": 1, "delivery_operator": 1, "hostess": 2},
        3: {"waiter": 8,  "loader": 1, "delivery_operator": 1, "hostess": 2},
        4: {"waiter": 11, "loader": 2, "delivery_operator": 1, "hostess": 2},
        5: {"waiter": 14, "loader": 3, "delivery_operator": 1, "hostess": 3},
        6: {"waiter": 11, "loader": 2, "delivery_operator": 1, "hostess": 2}
    }

    await state.update_data(daily_requirements=reqs)
    await run_solver_and_preview(callback, state)

async def run_solver_and_preview(event_message: Union[types.Message, types.CallbackQuery], state: FSMContext):
    data = await state.get_data()
    
    start_date_str = data.get("start_date")
    if not start_date_str:
        start_date_str = datetime.now().strftime("%Y-%m-%d")
        
    days_count = data.get("days_count", 7)
    reqs = data.get("daily_requirements", {})

    if isinstance(event_message, types.CallbackQuery):
        chat_id = event_message.message.chat.id
        await event_message.message.edit_text("⏳ <b>Формування розкладу з урахуванням вихідних співробітників...</b>", parse_mode="HTML")
    else:
        chat_id = event_message.chat.id

    schedule_res, status_msg = scheduler_solver.generate_schedule(
        start_date_str=start_date_str,
        days_count=days_count,
        daily_requirements=reqs
    )

    if not schedule_res:
        await state.clear()
        if isinstance(event_message, types.CallbackQuery):
            await event_message.message.edit_text(f"❌ Помилка генерації:\n{status_msg}")
        else:
            await event_message.answer(f"❌ Помилка генерації:\n{status_msg}")
        return

    await state.update_data(generated_schedule=schedule_res)
    await state.set_state(GenerateState.confirm_final)

    preview_text = f"📊 <b>ПОПЕРЕДНІЙ ПЕРЕГЛЯД ЗГЕНЕРОВАНОГО ГРАФІКУ ({days_count} ДНІВ):</b>\n\n"
    curr_date = ""

    for s in schedule_res:
        if s["formatted_date"] != curr_date:
            curr_date = s["formatted_date"]
            preview_text += f"\n📅 <b>{html.escape(curr_date)}</b>\n"
            
        full_name = str(s.get("full_name", "")).strip()
        role_code = s.get("role", "")
        role_t = ROLE_MAP.get(role_code, role_code)
        hours = s.get("shift_hours", "")

        if not full_name:
            continue

        if "ПОТРІБНА ПІДМОГА" in full_name:
            preview_text += f"  • ⚠️ <b>ПОТРІБНА ПІДМОГА</b> ({html.escape(role_t)}) — <code>{html.escape(hours)}</code>\n"
        else:
            preview_text += f"  • {html.escape(full_name)} ({html.escape(role_t)}) — <code>{html.escape(hours)}</code>\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Публікувати", callback_data="gen_publish_final"),
        InlineKeyboardButton(text="❌ Скасувати", callback_data="gen_cancel")
    ]])

    preview_text += "\n\nПублікувати та внести цей графік у Google Таблицю?"
    
    await send_long_message(chat_id, preview_text, reply_markup=kb)

@dp.callback_query(F.data == "gen_publish_final", GenerateState.confirm_final)
async def process_publish_final(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    generated_schedule = data.get("generated_schedule", [])

    database.save_generated_schedule(generated_schedule, overwrite=True)
    await state.clear()

    await callback.message.edit_text(
        "🎉 <b>Графік успішно збережено та опубліковано в Google Таблицю!</b>\n\n"
        "📩 Надсилаємо персональні сповіщення співробітникам...",
        parse_mode="HTML"
    )

    asyncio.create_task(notify_users_about_new_schedule(generated_schedule))

# --- ЗАМІНИ, СПИСОК СПІВРОБІТНИКІВ ТА ГРАФІКИ ---

@dp.message(F.text.in_({"🔄 Запропонувати заміну", "🔄 Запропонувати заміну (Днем / Годинами)"}))
async def start_swap_type_choice(message: types.Message, state: FSMContext):
    user_data = database.get_user_data(message.from_user.id)
    if not user_data:
        await message.answer("⚠️ Доступ обмежено.", reply_markup=get_guest_keyboard())
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Віддати зміну повністю (іншому колезі)", callback_data="swap_mode_full")],
        [InlineKeyboardButton(text="⏰ Помінятися ГОДИНАМИ/зміною (в той самий день)", callback_data="swap_mode_hours")]
    ])
    await state.set_state(SwapState.waiting_for_type)
    await message.answer("Оберіть тип заміни:", reply_markup=kb)

@dp.callback_query(F.data.startswith("swap_mode_"), SwapState.waiting_for_type)
async def process_swap_mode_choice(callback: types.CallbackQuery, state: FSMContext):
    mode = callback.data.replace("swap_mode_", "")
    await state.update_data(swap_mode=mode)

    shifts = database.get_user_schedule(callback.from_user.id, days_limit=14)
    if not shifts:
        await callback.message.edit_text("У вас немає майбутніх змін для заміни.")
        await state.clear()
        return

    builder = []
    now = datetime.now()

    for s in shifts:
        s_date_obj = datetime.strptime(s["date"], "%Y-%m-%d").date()
        
        if s_date_obj == now.date() and now.hour >= 10:
            continue
            
        role_title = ROLE_MAP.get(s.get("role"), s.get("role", ""))
        builder.append([InlineKeyboardButton(
            text=f"{s['formatted_date']} — {role_title} ({s['shift_hours']})", 
            callback_data=f"swap_shift_{s['date']}"
        )])

    if not builder:
        await callback.message.edit_text("❌ Дедлайн (10:00) для заміни змін на сьогодні вже минув!")
        await state.clear()
        return

    kb = InlineKeyboardMarkup(inline_keyboard=builder)
    await state.set_state(SwapState.waiting_for_shift)
    
    title = "повного віддавання" if mode == "full" else "обміну годинами"
    await callback.message.edit_text(f"Оберіть зміну для <b>{title}</b>:", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("swap_shift_"), SwapState.waiting_for_shift)
async def process_swap_shift_choice(callback: types.CallbackQuery, state: FSMContext):
    date_str = callback.data.replace("swap_shift_", "")
    data = await state.get_data()
    swap_mode = data.get("swap_mode", "full")

    now = datetime.now()
    s_date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    if s_date_obj == now.date() and now.hour >= 10:
        await callback.message.edit_text("❌ На жаль, заміну на сьогодні можна було замовити лише до 10:00 ранку!")
        await state.clear()
        return

    shifts = database.get_all_schedule(date_str=date_str)
    shift_role = None
    shift_hours = ""
    formatted_date = date_str

    for s in shifts:
        if str(s.get("telegram_id")) == str(callback.from_user.id):
            shift_role = s.get("role")
            shift_hours = s.get("shift_hours", "")
            formatted_date = s.get("formatted_date", date_str)
            break
            
    if not shift_role:
        user_data = database.get_user_data(callback.from_user.id)
        shift_role = user_data.get("role")

    await state.update_data(
        swap_date=date_str,
        shift_role=shift_role,
        shift_hours=shift_hours,
        formatted_date=formatted_date
    )

    if swap_mode == "full":
        colleagues = database.get_colleagues_for_swap(shift_role, callback.from_user.id, date_str)
        if not colleagues:
            await callback.message.edit_text("❌ На жаль, немає вільних колег для повної заміни.")
            await state.clear()
            return

        builder = []
        for c in colleagues:
            clean_c_name = str(c['full_name']).replace("•", "").replace("ПІБ:", "").strip()
            c_role = ROLE_MAP.get(c.get("role"), c.get("role"))
            builder.append([InlineKeyboardButton(
                text=f"👤 {clean_c_name} ({c_role})", 
                callback_data=f"swap_colleague_{c['telegram_id']}"
            )])
    else:
        colleagues_hours = database.get_colleagues_on_same_date(date_str, callback.from_user.id, shift_role)
        if not colleagues_hours:
            await callback.message.edit_text("❌ На цей день немає колег з інакшими годинами зміни для обміну.")
            await state.clear()
            return

        builder = []
        for ch in colleagues_hours:
            clean_c_name = str(ch['full_name']).replace("•", "").replace("ПІБ:", "").strip()
            builder.append([InlineKeyboardButton(
                text=f"👤 {clean_c_name} — {ch['shift_hours']}", 
                callback_data=f"swap_hours_colleague_{ch['telegram_id']}"
            )])

    kb = InlineKeyboardMarkup(inline_keyboard=builder)
    await state.set_state(SwapState.waiting_for_colleague)
    
    shift_role_title = ROLE_MAP.get(shift_role, shift_role)
    hours_text = f" (<code>{html.escape(shift_hours)}</code>)" if shift_hours else ""
    await callback.message.edit_text(
        f"Обрано зміну <b>{html.escape(shift_role_title)}</b>{hours_text} на <code>{html.escape(formatted_date)}</code>.\n\n"
        f"Оберіть колегу для обміну:", 
        reply_markup=kb, 
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("swap_hours_colleague_"), SwapState.waiting_for_colleague)
async def process_swap_hours_colleague_choice(callback: types.CallbackQuery, state: FSMContext):
    target_id = int(callback.data.replace("swap_hours_colleague_", ""))
    data = await state.get_data()
    date_str = data["swap_date"]
    shift_hours = data.get("shift_hours", "")
    formatted_date = data.get("formatted_date", date_str)

    from_user = database.get_user_data(callback.from_user.id)
    clean_from_name = str(from_user['full_name']).replace("•", "").replace("ПІБ:", "").strip()

    shifts = database.get_all_schedule(date_str=date_str)
    target_hours = ""
    for s in shifts:
        if str(s.get("telegram_id")) == str(target_id):
            target_hours = s.get("shift_hours", "")
            break

    await state.clear()
    
    await callback.message.edit_text("✅ Запит на обмін ГОДИНАМИ надіслано колезі! Очікуємо підтвердження.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Прийняти обмін", callback_data=f"acc_hswap_{date_str}_{callback.from_user.id}"),
        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"decline_swap_{date_str}_{callback.from_user.id}")
    ]])
    
    try:
        await bot.send_message(
            chat_id=target_id,
            text=f"⏰ <b>Запит на обмін ГОДИНАМИ роботи!</b>\n\n"
                 f"Колега <b>{html.escape(clean_from_name)}</b> просить помінятися часом зміни на <b>{html.escape(formatted_date)}</b>:\n"
                 f"• Його час: <code>{html.escape(shift_hours)}</code>\n"
                 f"• Ваші години: <code>{html.escape(target_hours)}</code>\n\n"
                 f"Ви згодні помінятися годинами?",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Помилка відправки обміну годинами колезі: {e}")

@dp.callback_query(F.data.startswith("acc_hswap_"))
async def process_hours_swap_accept_colleague(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    date_str = parts[2]
    from_id = int(parts[3])
    to_id = callback.from_user.id
    
    from_user = database.get_user_data(from_id)
    to_user = database.get_user_data(to_id)
    clean_from_name = str(from_user['full_name']).replace("•", "").replace("ПІБ:", "").strip()
    clean_to_name = str(to_user['full_name']).replace("•", "").replace("ПІБ:", "").strip()

    shifts = database.get_all_schedule(date_str=date_str)
    hours_from = ""
    hours_to = ""
    formatted_date = date_str

    for s in shifts:
        if str(s.get("telegram_id")) == str(from_id):
            hours_from = s.get("shift_hours", "")
            formatted_date = s.get("formatted_date", date_str)
        elif str(s.get("telegram_id")) == str(to_id):
            hours_to = s.get("shift_hours", "")

    await callback.message.edit_text("✅ Дякуємо! Запит передано на підтвердження Адміністратору.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Схвалити обмін", callback_data=f"admin_app_hswap_{date_str}_{from_id}_{to_id}"),
        InlineKeyboardButton(text="👎 Відхилити", callback_data=f"admin_reject_{date_str}_{from_id}_{to_id}")
    ]])
    
    await bot.send_message(
        chat_id=config.ADMIN_TELEGRAM_ID,
        text=f"⏰ <b>Запит на обмін ГОДИНАМИ (погоджено між співробітниками):</b>\n\n"
             f"• Дата: <b>{html.escape(formatted_date)}</b>\n"
             f"• <b>{html.escape(clean_from_name)}</b>: було <code>{html.escape(hours_from)}</code> ➡️ стане <code>{html.escape(hours_to)}</code>\n"
             f"• <b>{html.escape(clean_to_name)}</b>: було <code>{html.escape(hours_to)}</code> ➡️ стане <code>{html.escape(hours_from)}</code>\n\n"
             f"Затвердити обмін годинами?",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("admin_app_hswap_"))
async def process_admin_approve_hours_swap(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    date_str = parts[3]
    from_id = int(parts[4])
    to_id = int(parts[5])
    
    h1, h2 = database.swap_shift_hours_between_users(date_str, from_id, to_id)
    
    await callback.message.edit_text("✅ Обмін годинами успішно затверджено та внесено в Google Таблицю!")
    
    await bot.send_message(from_id, f"🎉 Адміністратор схвалив обмін годинами на <code>{date_str}</code>! Ваші нові години: <code>{h2}</code>.", parse_mode="HTML")
    await bot.send_message(to_id, f"🎉 Адміністратор схвалив обмін годинами на <code>{date_str}</code>! Ваші нові години: <code>{h1}</code>.", parse_mode="HTML")

@dp.callback_query(F.data.startswith("swap_colleague_"), SwapState.waiting_for_colleague)
async def process_swap_colleague_choice(callback: types.CallbackQuery, state: FSMContext):
    target_id = int(callback.data.replace("swap_colleague_", ""))
    data = await state.get_data()
    date_str = data["swap_date"]
    shift_role = data.get("shift_role", "")
    shift_hours = data.get("shift_hours", "")
    formatted_date = data.get("formatted_date", date_str)

    from_user = database.get_user_data(callback.from_user.id)
    clean_from_name = str(from_user['full_name']).replace("•", "").replace("ПІБ:", "").strip()
    shift_role_title = ROLE_MAP.get(shift_role, shift_role)
    hours_text = f" ({shift_hours})" if shift_hours else ""
    
    await state.clear()
    database.log_swap_request(date_str, callback.from_user.id, target_id, "pending_colleague")
    
    await callback.message.edit_text("✅ Запит на заміну надіслано колезі! Очікуємо підтвердження.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Прийняти", callback_data=f"accept_swap_{date_str}_{callback.from_user.id}"),
        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"decline_swap_{date_str}_{callback.from_user.id}")
    ]])
    
    try:
        await bot.send_message(
            chat_id=target_id,
            text=f"🔄 <b>Запит на заміну зміни!</b>\n\n"
                 f"Колега <b>{html.escape(clean_from_name)}</b> просить підмінити його на зміні:\n"
                 f"• Дата: <b>{html.escape(formatted_date)}</b>\n"
                 f"• Посада: <b>{html.escape(shift_role_title)}</b>{html.escape(hours_text)}\n\n"
                 f"Ви згодні взяти цю зміну?",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Помилка відправки колезі: {e}")

@dp.callback_query(F.data.startswith("decline_swap_"))
async def process_swap_decline(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    date_str = parts[2]
    from_id = int(parts[3])
    
    decliner_user = database.get_user_data(callback.from_user.id)
    clean_decliner_name = str(decliner_user['full_name']).replace("•", "").replace("ПІБ:", "").strip() if decliner_user else "Колега"

    await callback.message.edit_text("❌ Ви відхилили запит на заміну.")
    
    try:
        await bot.send_message(
            chat_id=from_id,
            text=f"❌ <b>Колега {html.escape(clean_decliner_name)}</b> відхилив(ла) ваш запит на заміну зміни на дату <code>{date_str}</code>.",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не вдалося надіслати сповіщення про відмову {from_id}: {e}")

@dp.callback_query(F.data.startswith("accept_swap_"))
async def process_swap_accept(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    date_str = parts[2]
    from_id = int(parts[3])
    to_id = callback.from_user.id
    
    from_user = database.get_user_data(from_id)
    to_user = database.get_user_data(to_id)
    clean_from_name = str(from_user['full_name']).replace("•", "").replace("ПІБ:", "").strip()
    clean_to_name = str(to_user['full_name']).replace("•", "").replace("ПІБ:", "").strip()

    shifts = database.get_all_schedule(date_str=date_str)
    shift_role_title = ""
    shift_hours = ""
    formatted_date = date_str
    for s in shifts:
        if str(s.get("telegram_id")) == str(from_id):
            shift_role_title = ROLE_MAP.get(s.get("role"), s.get("role", ""))
            shift_hours = s.get("shift_hours", "")
            formatted_date = s.get("formatted_date", date_str)
            break

    role_text = f"\n• Посада: <b>{html.escape(shift_role_title)}</b>" if shift_role_title else ""
    hours_text = f" ({shift_hours})" if shift_hours else ""
    
    await callback.message.edit_text("✅ Дякуємо! Запит передано на підтвердження Адміністратору.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Схвалити", callback_data=f"admin_approve_{date_str}_{from_id}_{to_id}"),
        InlineKeyboardButton(text="👎 Відхилити", callback_data=f"admin_reject_{date_str}_{from_id}_{to_id}")
    ]])
    
    await bot.send_message(
        chat_id=config.ADMIN_TELEGRAM_ID,
        text=f"⚙️ <b>Запит на заміну змін (погоджено між співробітниками):</b>\n\n"
             f"• Дата: <b>{html.escape(formatted_date)}</b>{role_text}{html.escape(hours_text)}\n"
             f"• Віддає зміну: <b>{html.escape(clean_from_name)}</b>\n"
             f"• Бере зміну: <b>{html.escape(clean_to_name)}</b>\n\n"
             f"Затвердити зміну графіку?",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("admin_approve_"))
async def process_admin_approve(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    date_str = parts[2]
    from_id = int(parts[3])
    to_id = int(parts[4])
    
    to_user = database.get_user_data(to_id)
    clean_to_name = str(to_user['full_name']).replace("•", "").replace("ПІБ:", "").strip()
    database.update_schedule_employee(date_str, from_id, to_id, clean_to_name)
    
    await callback.message.edit_text(f"✅ Заміну успішно затверджено і внесено в графік!")
    
    await bot.send_message(from_id, f"🎉 Адміністратор схвалив заміну змін на <code>{date_str}</code>!", parse_mode="HTML")
    await bot.send_message(to_id, f"🎉 Адміністратор схвалив заміну. Зміну на <code>{date_str}</code> додано у ваш графік!", parse_mode="HTML")

@dp.callback_query(F.data.startswith("admin_reject_"))
async def process_admin_reject(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    date_str = parts[2]
    from_id = int(parts[3])
    to_id = int(parts[4])

    await callback.message.edit_text("❌ Заміну відхилено адміністратором.")
    
    try:
        await bot.send_message(from_id, f"❌ Адміністратор <b>відхилив</b> заміну зміни на <code>{date_str}</code>.", parse_mode="HTML")
        await bot.send_message(to_id, f"❌ Адміністратор <b>відхилив</b> заміну зміни на <code>{date_str}</code>.", parse_mode="HTML")
    except Exception as e:
        logging.error(f"Не вдалося сповістити про відмову адміна: {e}")

@dp.message(F.text == "👥 Список співробітників")
async def list_employees(message: types.Message):
    user_data = database.get_user_data(message.from_user.id)
    if not user_data or user_data.get("role") != "admin":
        return

    employees = database.get_all_employees()
    if not employees:
        await message.answer("У системі поки немає зареєстрованих співробітників.")
        return

    builder = []
    text = "👥 <b>Список співробітників:</b>\n\n"
    for emp in employees:
        emp_id = emp.get("telegram_id")
        emp_name = str(emp.get("full_name", "")).replace("•", "").replace("ПІБ:", "").strip()
        emp_phone = str(emp.get("phone", ""))
        emp_role = ROLE_MAP.get(emp.get("role"), emp.get("role"))
        
        phone_text = f" (<code>{html.escape(emp_phone)}</code>)" if emp_phone else ""
        text += f"• <b>{html.escape(emp_name)}</b> ({html.escape(str(emp_role))}){phone_text} — ID: <code>{emp_id}</code>\n"
        
        if str(emp_id) != str(config.ADMIN_TELEGRAM_ID):
            builder.append([InlineKeyboardButton(
                text=f"❌ Видалити: {emp_name}",
                callback_data=f"del_emp_{emp_id}"
            )])

    kb = InlineKeyboardMarkup(inline_keyboard=builder) if builder else None
    await send_long_message(message.chat.id, text, reply_markup=kb)

@dp.callback_query(F.data.startswith("del_emp_"))
async def process_delete_employee(callback: types.CallbackQuery):
    emp_id = int(callback.data.replace("del_emp_", ""))
    emp_data = database.get_user_data(emp_id)
    
    if not emp_data:
        await callback.message.edit_text("❌ Співробітника не знайдено.")
        return

    emp_name = str(emp_data.get("full_name", "")).replace("•", "").replace("ПІБ:", "").strip()
    success = database.delete_employee_by_id(emp_id)
    
    if success:
        await callback.message.edit_text(f"✅ Співробітника <b>{html.escape(emp_name)}</b> (ID: <code>{emp_id}</code>) успішно видалено з системи!", parse_mode="HTML")
    else:
        await callback.message.edit_text("❌ Помилка під час видалення з Google Таблиці.")

@dp.message(F.text == "📅 Мій графік на тиждень")
async def my_schedule_week(message: types.Message):
    user_data = database.get_user_data(message.from_user.id)
    if not user_data:
        await message.answer("⚠️ Доступ обмежено. Надішліть заявку на реєстрацію.", reply_markup=get_guest_keyboard())
        return

    shifts = database.get_user_schedule(message.from_user.id, days_limit=7)
    if not shifts:
        await message.answer("У вас немає запланованих змін на найближчий тиждень.")
        return
    
    text = "🗓 <b>Ваш графік на 7 днів:</b>\n\n"
    for s in shifts:
        role_title = ROLE_MAP.get(s.get("role"), s.get("role", ""))
        text += f"• <b>{html.escape(s['formatted_date'])}</b> — <code>{html.escape(s['shift_hours'])}</code> ({html.escape(role_title)})\n"
    await send_long_message(message.chat.id, text)

@dp.message(F.text == "🗓 Мій графік на весь період")
async def my_schedule_all(message: types.Message):
    user_data = database.get_user_data(message.from_user.id)
    if not user_data:
        await message.answer("⚠️ Доступ обмежено. Надішліть заявку на реєстрацію.", reply_markup=get_guest_keyboard())
        return

    shifts = database.get_user_schedule(message.from_user.id)
    if not shifts:
        await message.answer("У вас немає запланованих змін у системі.")
        return
    
    text = "🗓 <b>Ваш графік на весь період:</b>\n\n"
    for s in shifts:
        role_title = ROLE_MAP.get(s.get("role"), s.get("role", ""))
        text += f"• <b>{html.escape(s['formatted_date'])}</b> — <code>{html.escape(s['shift_hours'])}</code> ({html.escape(role_title)})\n"
    await send_long_message(message.chat.id, text)

@dp.message(F.text == "📅 Графік усіх на тиждень")
async def admin_schedule_week(message: types.Message):
    user_data = database.get_user_data(message.from_user.id)
    if not user_data or user_data.get("role") != "admin":
        return

    shifts = database.get_all_schedule(days_limit=7)
    if not shifts:
        await message.answer("На найближчий тиждень графік порожній.")
        return

    text = "📊 <b>Загальний графік на 7 днів:</b>\n\n"
    current_date = ""
    for s in shifts:
        if s['formatted_date'] != current_date:
            current_date = s['formatted_date']
            text += f"\n📅 <b>{html.escape(current_date)}</b>\n"
        clean_name = str(s['full_name']).replace("•", "").replace("ПІБ:", "").strip()
        role = ROLE_MAP.get(s.get('role'), s.get('role'))
        text += f"  • {html.escape(clean_name)} ({html.escape(str(role))}): <code>{html.escape(s['shift_hours'])}</code>\n"

    await send_long_message(message.chat.id, text)

@dp.message(F.text == "🗓 Графік усіх на весь період")
async def admin_schedule_all(message: types.Message):
    user_data = database.get_user_data(message.from_user.id)
    if not user_data or user_data.get("role") != "admin":
        return

    shifts = database.get_all_schedule()
    if not shifts:
        await message.answer("Графік у системі порожній.")
        return

    text = "📊 <b>Загальний графік на весь період:</b>\n\n"
    current_date = ""
    for s in shifts:
        if s['formatted_date'] != current_date:
            current_date = s['formatted_date']
            text += f"\n📅 <b>{html.escape(current_date)}</b>\n"
        clean_name = str(s['full_name']).replace("•", "").replace("ПІБ:", "").strip()
        role = ROLE_MAP.get(s.get('role'), s.get('role'))
        text += f"  • {html.escape(clean_name)} ({html.escape(str(role))}): <code>{html.escape(s['shift_hours'])}</code>\n"

    await send_long_message(message.chat.id, text)

@dp.message(F.text == "🔍 Графік на дату")
async def admin_search_date_start(message: types.Message):
    user_data = database.get_user_data(message.from_user.id)
    if not user_data or user_data.get("role") != "admin":
        return

    await message.answer("Оберіть потрібну дату у календарі:", reply_markup=get_uk_calendar(max_days_ahead=None, allow_past=True))

@dp.callback_query(UkCalendarCallback.filter())
async def process_uk_calendar(callback_query: types.CallbackQuery, callback_data: UkCalendarCallback, state: FSMContext):
    action = callback_data.action

    if action == "IGNORE":
        await callback_query.answer()
        return

    current_fsm_state = await state.get_state()
    is_generating = (current_fsm_state == GenerateState.choose_start_date.state)

    if action in ["PREV-MONTH", "NEXT-MONTH"]:
        with suppress(TelegramBadRequest):
            if is_generating:
                kb = get_uk_calendar(year=callback_data.year, month=callback_data.month, max_days_ahead=30, allow_past=False)
            else:
                kb = get_uk_calendar(year=callback_data.year, month=callback_data.month, max_days_ahead=None, allow_past=True)
            await callback_query.message.edit_reply_markup(reply_markup=kb)
        await callback_query.answer()
        return

    if action in ["DAY", "TODAY"]:
        if not is_generating:
            date_str = f"{callback_data.year:04d}-{callback_data.month:02d}-{callback_data.day:02d}"
            shifts = database.get_all_schedule(date_str=date_str)

            if not shifts:
                formatted_input_date = f"{callback_data.day:02d}.{callback_data.month:02d}.{callback_data.year}"
                await callback_query.message.edit_text(f"📅 На дату <b>{formatted_input_date}</b> змін не знайдено.", parse_mode="HTML")
                return

            formatted_header = shifts[0]["formatted_date"]
            text = f"📊 <b>Графік на {html.escape(formatted_header)}:</b>\n\n"
            for s in shifts:
                clean_name = str(s['full_name']).replace("•", "").replace("ПІБ:", "").strip()
                role = ROLE_MAP.get(s.get('role'), s.get('role'))
                text += f"• <b>{html.escape(clean_name)}</b> ({html.escape(str(role))}): <code>{html.escape(s['shift_hours'])}</code>\n"

            await send_long_message(callback_query.message.chat.id, text)

async def main():
    scheduler.add_job(send_daily_reminders, 'cron', hour=20, minute=0)
    scheduler.add_job(auto_clear_old_data_job, 'cron', hour=23, minute=59)
    scheduler.start()
    
    asyncio.create_task(auto_clear_old_data_job())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
