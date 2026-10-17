import asyncio
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
from cachetools import TTLCache
import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

DAYS_OF_WEEK_UKR = {
    0: "Понеділок",
    1: "Вівторок",
    2: "Середа",
    3: "Четвер",
    4: "П'ятниця",
    5: "Субота",
    6: "Неділя"
}

ROLE_MAP_TITLE = {
    "waiter": "Офіціанти",
    "hostess": "Хостес",
    "loader": "Вантажники",
    "delivery_operator": "Оператори доставки"
}

_cache = TTLCache(maxsize=50, ttl=30)

def format_date_with_weekday(date_obj) -> str:
    weekday_str = DAYS_OF_WEEK_UKR.get(date_obj.weekday(), "")
    return f"{date_obj.strftime('%d.%m.%Y')} ({weekday_str})"

def clear_cache():
    _cache.clear()

def _get_sheet_sync():
    creds = Credentials.from_service_account_file(config.JSON_KEY_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open(config.GOOGLE_SHEET_NAME)

def _get_records_cached(worksheet_name: str):
    cache_key = f"ws_records_{worksheet_name}"
    if cache_key in _cache:
        return _cache[cache_key]

    sheet = _get_sheet_sync()
    ws = sheet.worksheet(worksheet_name)
    records = ws.get_all_records()
    _cache[cache_key] = records
    return records

def _init_admin_sync():
    try:
        records = _get_records_cached("Співробітники")
        for record in records:
            if str(record.get("telegram_id")) == str(config.ADMIN_TELEGRAM_ID):
                return
        
        sheet = _get_sheet_sync()
        ws = sheet.worksheet("Співробітники")
        ws.append_row([str(config.ADMIN_TELEGRAM_ID), "Головний Адміністратор", "admin", ""])
        clear_cache()
    except Exception as e:
        print(f"Помилка ініціалізації адміна: {e}")

def init_admin_if_not_exists():
    asyncio.create_task(asyncio.to_thread(_init_admin_sync))

def add_employee(telegram_id: int, full_name: str, role: str, phone: str = ""):
    try:
        sheet = _get_sheet_sync()
        ws = sheet.worksheet("Співробітники")
        ws.append_row([str(telegram_id), full_name, role, phone])
        clear_cache()
    except Exception as e:
        print(f"Помилка додавання співробітника: {e}")

def get_user_data(telegram_id: int):
    records = _get_records_cached("Співробітники")
    for record in records:
        if str(record.get("telegram_id")) == str(telegram_id):
            return record
    return None

def get_all_employees():
    return _get_records_cached("Співробітники")

def delete_employee_by_id(telegram_id: int):
    sheet = _get_sheet_sync()
    ws = sheet.worksheet("Співробітники")
    records = ws.get_all_records()
    
    for idx, record in enumerate(records, start=2):
        if str(record.get("telegram_id")) == str(telegram_id):
            ws.delete_rows(idx)
            clear_cache()
            return True
    return False

def clear_old_schedules(days_offset: int = 2):
    pass

def clear_old_wishes(days_offset: int = 7):
    def _sync():
        try:
            sheet = _get_sheet_sync()
            try:
                ws = sheet.worksheet("Запит_Вихідні")
            except Exception:
                return

            all_rows = ws.get_all_values()
            if len(all_rows) <= 1:
                return

            header = all_rows[0]
            cutoff_date = (datetime.now() - timedelta(days=days_offset)).date()
            
            filtered_rows = [header]
            removed_count = 0

            for row in all_rows[1:]:
                if not row or not any(row) or not row[0]:
                    continue
                try:
                    row_date = datetime.strptime(row[0], "%Y-%m-%d").date()
                    if row_date >= cutoff_date:
                        filtered_rows.append(row)
                    else:
                        removed_count += 1
                except ValueError:
                    filtered_rows.append(row)

            if removed_count > 0:
                ws.clear()
                ws.update("A1", filtered_rows)
                clear_cache()
                print(f"🧹 Очищено {removed_count} застарілих записів із 'Запит_Вихідні'.")
        except Exception as e:
            print(f"Помилка під час очищення застарілих вихідних: {e}")

    asyncio.create_task(asyncio.to_thread(_sync))

def save_user_wish_off_dates(telegram_id: int, full_name: str, role: str, off_dates: set):
    def _sync():
        try:
            sheet = _get_sheet_sync()
            try:
                ws = sheet.worksheet("Запит_Вихідні")
            except Exception:
                ws = sheet.add_worksheet(title="Запит_Вихідні", rows="100", cols="5")
                ws.append_row(["date", "telegram_id", "full_name", "role", "type"])

            all_rows = ws.get_all_values()
            header = all_rows[0] if all_rows else ["date", "telegram_id", "full_name", "role", "type"]
            
            filtered_rows = [header]
            if len(all_rows) > 1:
                for row in all_rows[1:]:
                    if row and len(row) > 1 and str(row[1]) == str(telegram_id):
                        continue
                    if row and any(row):
                        filtered_rows.append(row)

            for date_str in sorted(off_dates):
                filtered_rows.append([date_str, str(telegram_id), full_name, role, "обов'язковий вихідний"])

            ws.clear()
            ws.update("A1", filtered_rows)
            clear_cache()
        except Exception as e:
            print(f"Помилка під час збереження вихідних у Google Таблицю: {e}")

    asyncio.create_task(asyncio.to_thread(_sync))

def load_all_wishes_from_sheet():
    try:
        records = _get_records_cached("Запит_Вихідні")
        wishes_map = {}
        for r in records:
            t_id = str(r.get("telegram_id"))
            d_str = str(r.get("date"))
            if t_id and d_str:
                if t_id not in wishes_map:
                    wishes_map[t_id] = {"off_dates": set(), "shift_prefs": {}, "notes": ""}
                wishes_map[t_id]["off_dates"].add(d_str)
        return wishes_map
    except Exception:
        return {}

def save_generated_schedule(generated_shifts: list, overwrite: bool = True):
    if not generated_shifts:
        return

    sheet = _get_sheet_sync()
    ws = sheet.worksheet("Графік")

    unique_dates = sorted(list(set(s["date"] for s in generated_shifts if s.get("date"))))
    header_row = ["", ""] + [format_date_with_weekday(datetime.strptime(d, "%Y-%m-%d").date()) for d in unique_dates]

    employees = get_all_employees()
    roles_order = ["waiter", "hostess", "loader", "delivery_operator"]

    shift_map = {}
    for s in generated_shifts:
        key = (str(s.get("telegram_id")), s.get("date"))
        shift_map[key] = s.get("shift_hours", "")

    matrix_rows = [header_row]

    for role_code in roles_order:
        role_emps = [e for e in employees if e.get("role") == role_code]
        if not role_emps:
            continue

        matrix_rows.append([ROLE_MAP_TITLE.get(role_code, role_code), "", ""] + [""] * (len(unique_dates) - 1))

        for emp in role_emps:
            emp_id = str(emp.get("telegram_id"))
            emp_name = str(emp.get("full_name", "")).replace("•", "").replace("ПІБ:", "").strip()
            
            row = [emp_id, emp_name]
            for d_str in unique_dates:
                hours = shift_map.get((emp_id, d_str), "")
                row.append(hours if hours else "Вихідний")
            
            matrix_rows.append(row)

        sub_slots = [s for s in generated_shifts if s.get("role") == role_code and str(s.get("telegram_id")) == "0"]
        if sub_slots:
            sub_map = {}
            for s in sub_slots:
                d_str = s.get("date")
                if d_str not in sub_map:
                    sub_map[d_str] = []
                sub_map[d_str].append(s.get("shift_hours", ""))

            max_subs = max([len(v) for v in sub_map.values()], default=0)
            for sub_idx in range(max_subs):
                row = ["0", "⚠️ ПОТРІБНА ПІДМОГА"]
                for d_str in unique_dates:
                    hours_list = sub_map.get(d_str, [])
                    hours = hours_list[sub_idx] if sub_idx < len(hours_list) else ""
                    row.append(hours if hours else "Вихідний")
                matrix_rows.append(row)

    ws.clear()
    ws.update("A1", matrix_rows)
    clear_cache()

def get_parsed_schedule_from_matrix():
    try:
        sheet = _get_sheet_sync()
        ws = sheet.worksheet("Графік")
        all_values = ws.get_all_values()
        
        if len(all_values) < 2:
            return []

        header = all_values[0]
        dates_in_header = []
        for col_idx in range(2, len(header)):
            cell_val = header[col_idx]
            if "(" in cell_val:
                raw_d = cell_val.split("(")[0].strip()
                try:
                    d_obj = datetime.strptime(raw_d, "%d.%m.%Y").date()
                    dates_in_header.append((col_idx, d_obj.strftime("%Y-%m-%d"), cell_val))
                except ValueError:
                    continue

        parsed_shifts = []
        current_role = None

        for row in all_values[1:]:
            if not row or not any(row):
                continue
            
            col_a = str(row[0]).strip()
            col_b = str(row[1]).strip() if len(row) > 1 else ""

            if col_a in ROLE_MAP_TITLE.values():
                for code, title in ROLE_MAP_TITLE.items():
                    if title == col_a:
                        current_role = code
                        break
                continue

            if col_a and (col_a.isdigit() or col_a == "0"):
                emp_id = col_a
                emp_name = col_b

                for col_idx, date_str, fmt_date in dates_in_header:
                    if col_idx < len(row):
                        cell_hours = str(row[col_idx]).strip()
                        if cell_hours and cell_hours.lower() != "вихідний":
                            parsed_shifts.append({
                                "date": date_str,
                                "formatted_date": fmt_date,
                                "telegram_id": emp_id,
                                "full_name": emp_name,
                                "role": current_role or "waiter",
                                "shift_hours": cell_hours,
                                "raw_date_obj": datetime.strptime(date_str, "%Y-%m-%d").date()
                            })

        return parsed_shifts
    except Exception as e:
        print(f"Помилка зчитування матриці графіку: {e}")
        return []

def get_user_schedule(telegram_id: int, days_limit: int = None):
    all_shifts = get_parsed_schedule_from_matrix()
    today = datetime.now().date()
    user_shifts = []

    for s in all_shifts:
        if str(s.get("telegram_id")) == str(telegram_id):
            shift_date = s["raw_date_obj"]
            if shift_date >= today:
                if days_limit and shift_date > (today + timedelta(days=days_limit)):
                    continue
                user_shifts.append(s)

    user_shifts.sort(key=lambda x: x["raw_date_obj"])
    return user_shifts

def get_all_schedule(date_str: str = None, days_limit: int = None):
    all_shifts = get_parsed_schedule_from_matrix()
    today = datetime.now().date()
    result = []

    for s in all_shifts:
        shift_date = s["raw_date_obj"]
        if date_str:
            if s.get("date") == date_str:
                result.append(s)
        else:
            if shift_date >= today:
                if days_limit and shift_date > (today + timedelta(days=days_limit)):
                    continue
                result.append(s)

    result.sort(key=lambda x: (x["raw_date_obj"], x.get("role", "")))
    return result

def get_tomorrow_shifts():
    all_shifts = get_parsed_schedule_from_matrix()
    tomorrow_str = (datetime.now().date() + timedelta(days=1)).strftime("%Y-%m-%d")
    return [s for s in all_shifts if s.get("date") == tomorrow_str and str(s.get("telegram_id")) != "0"]

def get_busy_user_ids_for_date(date_str: str) -> set:
    all_shifts = get_parsed_schedule_from_matrix()
    return set(str(s.get("telegram_id")) for s in all_shifts if s.get("date") == date_str and str(s.get("telegram_id")) != "0")

def get_colleagues_for_swap(shift_role: str, exclude_id: int, date_str: str):
    records = _get_records_cached("Співробітники")
    busy_ids = get_busy_user_ids_for_date(date_str)

    allowed_roles = [shift_role]
    if shift_role == "waiter":
        allowed_roles = ["waiter"]
    elif shift_role == "hostess":
        allowed_roles = ["hostess", "waiter", "delivery_operator"]

    colleagues = []
    for r in records:
        r_role = r.get("role")
        r_id = str(r.get("telegram_id"))
        if r_role in allowed_roles and r_id != str(exclude_id) and r_role != "admin":
            if r_id not in busy_ids:
                colleagues.append(r)

    return colleagues

def get_colleagues_on_same_date(date_str: str, exclude_id: int, role: str):
    all_shifts = get_parsed_schedule_from_matrix()
    my_shift_hours = None
    
    for s in all_shifts:
        if s.get("date") == date_str and str(s.get("telegram_id")) == str(exclude_id):
            my_shift_hours = s.get("shift_hours")
            break

    working_colleagues = []
    for s in all_shifts:
        r_id = str(s.get("telegram_id"))
        r_role = s.get("role")
        r_hours = s.get("shift_hours")
        
        if s.get("date") == date_str and r_id != str(exclude_id) and r_id != "0" and r_role == role:
            if r_hours != my_shift_hours:
                working_colleagues.append({
                    "telegram_id": r_id,
                    "full_name": s.get("full_name"),
                    "role": r_role,
                    "shift_hours": r_hours,
                    "my_hours": my_shift_hours
                })

    return working_colleagues

def update_schedule_employee(date_str: str, old_id: int, new_id: int, new_name: str):
    """
    Правильна матрична заміна:
    1. Переносить години зміни від old_id до new_id у колонці конкретної дати.
    2. Встановлює 'Вихідний' для old_id на цю дату.
    """
    sheet = _get_sheet_sync()
    ws = sheet.worksheet("Графік")
    all_rows = ws.get_all_values()
    if not all_rows:
        return

    header = all_rows[0]
    target_col = None
    
    # Знаходимо потрібну колонку дати
    for col_idx in range(2, len(header)):
        cell_val = header[col_idx]
        if date_str in cell_val or format_date_with_weekday(datetime.strptime(date_str, "%Y-%m-%d").date()) in cell_val:
            target_col = col_idx + 1 # 1-based index для gspread
            break

    if target_col:
        old_row_idx = None
        new_row_idx = None
        shift_hours = None

        # Знаходимо рядки старої та нової людини
        for row_idx, r in enumerate(all_rows, start=1):
            if len(r) > 0:
                if str(r[0]) == str(old_id):
                    old_row_idx = row_idx
                    shift_hours = r[target_col - 1] if target_col <= len(r) else ""
                elif str(r[0]) == str(new_id):
                    new_row_idx = row_idx

        # Переносимо тільки комірку потрібної дати
        if old_row_idx and new_row_idx and shift_hours:
            ws.update_cell(old_row_idx, target_col, "Вихідний")
            ws.update_cell(new_row_idx, target_col, shift_hours)

    clear_cache()

def swap_shift_hours_between_users(date_str: str, user1_id: int, user2_id: int):
    sheet = _get_sheet_sync()
    ws = sheet.worksheet("Графік")
    all_rows = ws.get_all_values()
    if not all_rows:
        return None, None

    header = all_rows[0]
    target_col = None
    for col_idx in range(2, len(header)):
        cell_v = header[col_idx]
        if date_str in cell_v or format_date_with_weekday(datetime.strptime(date_str, "%Y-%m-%d").date()) in cell_v:
            target_col = col_idx + 1
            break

    if target_col:
        row1_idx = None
        row2_idx = None
        hours1 = None
        hours2 = None

        for row_idx, r in enumerate(all_rows, start=1):
            if len(r) > 0 and str(r[0]) == str(user1_id):
                row1_idx = row_idx
                hours1 = r[target_col - 1] if target_col <= len(r) else ""
            elif len(r) > 0 and str(r[0]) == str(user2_id):
                row2_idx = row_idx
                hours2 = r[target_col - 1] if target_col <= len(r) else ""

        if row1_idx and row2_idx and hours1 and hours2:
            ws.update_cell(row1_idx, target_col, hours2)
            ws.update_cell(row2_idx, target_col, hours1)
            clear_cache()
            return hours1, hours2

    return None, None

def log_swap_request(date_str: str, from_id: int, to_id: int, status: str = "pending"):
    try:
        sheet = _get_sheet_sync()
        ws = sheet.worksheet("Журнал_Замін")
        ws.append_row([date_str, str(from_id), str(to_id), "заміна", status, ""])
        clear_cache()
    except Exception:
        pass
