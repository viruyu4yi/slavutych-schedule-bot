import calendar
from datetime import datetime, timedelta
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters.callback_data import CallbackData

class UkCalendarCallback(CallbackData, prefix="uk_cal"):
    action: str
    year: int
    month: int
    day: int

UK_MONTHS = [
    "", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
    "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"
]

UK_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

def get_uk_calendar(year: int = None, month: int = None, max_days_ahead: int = 30, allow_past: bool = False) -> InlineKeyboardMarkup:
    today = datetime.now().date()
    
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    max_allowed_date = today + timedelta(days=max_days_ahead) if max_days_ahead is not None else None

    keyboard = []

    # Визначаємо попередній та наступний місяці
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    # Заголовок календаря з навігацією
    header_row = [
        InlineKeyboardButton(text="‹", callback_data=UkCalendarCallback(action="PREV-MONTH", year=prev_year, month=prev_month, day=1).pack()),
        InlineKeyboardButton(text=f"{UK_MONTHS[month]} {year}", callback_data=UkCalendarCallback(action="IGNORE", year=year, month=month, day=1).pack()),
        InlineKeyboardButton(text="›", callback_data=UkCalendarCallback(action="NEXT-MONTH", year=next_year, month=next_month, day=1).pack())
    ]
    keyboard.append(header_row)

    # Дні тижня
    week_days_row = [InlineKeyboardButton(text=day, callback_data=UkCalendarCallback(action="IGNORE", year=year, month=month, day=1).pack()) for day in UK_DAYS]
    keyboard.append(week_days_row)

    # Дні місяця
    cal = calendar.monthcalendar(year, month)
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data=UkCalendarCallback(action="IGNORE", year=year, month=month, day=1).pack()))
            else:
                date_obj = datetime(year, month, day).date()
                
                is_past = date_obj < today and not allow_past
                is_too_far = max_allowed_date is not None and date_obj > max_allowed_date

                if is_past or is_too_far:
                    row.append(InlineKeyboardButton(text="❌", callback_data=UkCalendarCallback(action="IGNORE", year=year, month=month, day=day).pack()))
                else:
                    day_text = f"[{day}]" if date_obj == today else str(day)
                    row.append(InlineKeyboardButton(text=day_text, callback_data=UkCalendarCallback(action="DAY", year=year, month=month, day=day).pack()))
        keyboard.append(row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)
