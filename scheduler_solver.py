from ortools.sat.python import cp_model
from datetime import datetime, timedelta
import database
import wishes_db

SHIFT_HOURS_MAP = {
    0: {
        "waiter": ["11:00 - 22:00", "11:00 - 22:00", "16:00 - 23:00", "16:00 - 23:00", "16:00 - 23:00", "16:00 - 23:00"],
        "hostess": ["11:00 - 21:00", "16:00 - 22:00"],
        "loader": ["10:00 - 22:00"],
        "delivery_operator": ["11:00 - 22:00"]
    },
    1: {
        "waiter": ["11:00 - 22:00", "11:00 - 22:00", "16:00 - 23:00", "16:00 - 23:00", "16:00 - 23:00", "16:00 - 23:00"],
        "hostess": ["11:00 - 21:00", "16:00 - 22:00"],
        "loader": ["10:00 - 22:00"],
        "delivery_operator": ["11:00 - 22:00"]
    },
    2: {
        "waiter": ["11:00 - 22:00", "11:00 - 22:00", "11:00 - 22:00", "16:00 - 23:00", "16:00 - 23:00", "16:00 - 23:00", "16:00 - 23:00", "16:00 - 23:00"],
        "hostess": ["11:00 - 21:00", "16:00 - 22:00"],
        "loader": ["10:00 - 22:00"],
        "delivery_operator": ["11:00 - 22:00"]
    },
    3: {
        "waiter": ["11:00 - 22:00", "11:00 - 22:00", "11:00 - 22:00", "16:00 - 23:00", "16:00 - 23:00", "16:00 - 23:00", "16:00 - 23:00", "16:00 - 23:00"],
        "hostess": ["11:00 - 21:00", "16:00 - 22:00"],
        "loader": ["10:00 - 22:00"],
        "delivery_operator": ["11:00 - 22:00"]
    },
    4: {
        "waiter": ["11:00 - 22:00"] * 5 + ["16:00 - 23:00"] * 6,
        "hostess": ["11:00 - 21:30", "14:00 - 22:00"],
        "loader": ["10:00 - 22:00", "10:00 - 22:00"],
        "delivery_operator": ["11:00 - 22:00"]
    },
    5: {
        "waiter": ["10:00 - 23:00"] * 14,
        "hostess": ["10:00 - 21:00", "11:00 - 21:30", "14:00 - 22:00", "14:00 - 22:00"],
        "loader": ["09:30 - 22:00", "09:30 - 22:00", "09:30 - 22:00"],
        "delivery_operator": ["11:00 - 22:00"]
    },
    6: {
        "waiter": ["11:00 - 22:00"] * 6 + ["16:00 - 23:00"] * 5,
        "hostess": ["11:00 - 21:30", "11:00 - 22:00"],
        "loader": ["10:00 - 22:00", "10:00 - 22:00"],
        "delivery_operator": ["11:00 - 22:00"]
    }
}

def get_hours_for_slot(w_day: int, role: str, slot_idx: int) -> str:
    day_shifts = SHIFT_HOURS_MAP.get(w_day, {})
    role_hours = day_shifts.get(role, [])
    if slot_idx < len(role_hours):
        return role_hours[slot_idx]
    
    defaults = {
        "waiter": "11:00 - 22:00",
        "hostess": "11:00 - 21:30",
        "loader": "10:00 - 22:00" if w_day != 5 else "09:30 - 22:00",
        "delivery_operator": "11:00 - 22:00"
    }
    return defaults.get(role, "10:00 - 22:00")

def generate_schedule(start_date_str: str, days_count: int, daily_requirements: dict):
    model = cp_model.CpModel()
    
    employees = database.get_all_employees()
    if not employees:
        return None, "У системі немає зареєстрованих співробітників."

    emp_list = [e for e in employees if e.get("role") != "admin"]
    if not emp_list:
        return None, "Немає доступних працівників для формування графіку."

    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    days = [start_date + timedelta(days=i) for i in range(days_count)]

    shifts = {}
    for e in emp_list:
        emp_id = str(e.get("telegram_id"))
        for d_idx in range(len(days)):
            shifts[(emp_id, d_idx)] = model.NewBoolVar(f"shift_{emp_id}_{d_idx}")

    # Завантажуємо вихідні з пам'яті або з Google Таблиці
    all_wishes = wishes_db.get_all_wishes()
    sheet_wishes = database.load_all_wishes_from_sheet()

    for e in emp_list:
        emp_id = str(e.get("telegram_id"))
        e_wishes = all_wishes.get(emp_id, {})
        off_dates = e_wishes.get("off_dates", set())

        # Якщо в пам'яті порожньо, беремо з Google Таблиці
        if not off_dates and emp_id in sheet_wishes:
            off_dates = sheet_wishes[emp_id].get("off_dates", set())

        for d_idx, d in enumerate(days):
            date_str = d.strftime("%Y-%m-%d")
            if date_str in off_dates:
                model.Add(shifts[(emp_id, d_idx)] == 0)

    shortages = {}
    roles = ["waiter", "hostess", "loader", "delivery_operator"]

    for d_idx, d in enumerate(days):
        w_day = d.weekday()
        reqs_for_day = daily_requirements.get(w_day, {})
        for r in roles:
            needed = reqs_for_day.get(r, 0)
            shortages[(r, d_idx)] = model.NewIntVar(0, needed, f"shortage_{r}_{d_idx}")

    for d_idx, d in enumerate(days):
        w_day = d.weekday()
        reqs_for_day = daily_requirements.get(w_day, {})
        for r in roles:
            needed = reqs_for_day.get(r, 0)
            qualified_emps = [str(e["telegram_id"]) for e in emp_list if e.get("role") == r]
            assigned_vars = [shifts[(e_id, d_idx)] for e_id in qualified_emps]
            model.Add(sum(assigned_vars) + shortages[(r, d_idx)] == needed)

    for e in emp_list:
        e_id = str(e.get("telegram_id"))
        for d_idx in range(len(days)):
            model.Add(shifts[(e_id, d_idx)] <= 1)

    for e in emp_list:
        e_id = str(e.get("telegram_id"))
        for d_idx in range(len(days) - 6):
            model.Add(sum(shifts[(e_id, d_idx + i)] for i in range(7)) <= 6)

    penalty_terms = []
    for key, var in shortages.items():
        penalty_terms.append(var * 1000)

    shift_counts = []
    for e in emp_list:
        e_id = str(e.get("telegram_id"))
        count = model.NewIntVar(0, len(days), f"count_{e_id}")
        model.Add(count == sum(shifts[(e_id, d_idx)] for d_idx in range(len(days))))
        shift_counts.append(count)

    max_shifts = model.NewIntVar(0, len(days), "max_shifts")
    min_shifts = model.NewIntVar(0, len(days), "min_shifts")
    model.AddMaxEquality(max_shifts, shift_counts)
    model.AddMinEquality(min_shifts, shift_counts)

    penalty_terms.append((max_shifts - min_shifts) * 10)
    model.Minimize(sum(penalty_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        result_schedule = []
        for d_idx, d in enumerate(days):
            w_day = d.weekday()
            formatted_date = database.format_date_with_weekday(d)
            date_str = d.strftime("%Y-%m-%d")

            for r in roles:
                slot_counter = 0
                qualified_emps = [e for e in emp_list if e.get("role") == r]
                
                for e in qualified_emps:
                    e_id = str(e.get("telegram_id"))
                    if solver.Value(shifts[(e_id, d_idx)]) == 1:
                        shift_hours = get_hours_for_slot(w_day, r, slot_counter)
                        slot_counter += 1
                        clean_name = str(e.get("full_name", "")).replace("•", "").replace("ПІБ:", "").strip()
                        result_schedule.append({
                            "date": date_str,
                            "formatted_date": formatted_date,
                            "telegram_id": e_id,
                            "full_name": clean_name,
                            "role": r,
                            "shift_hours": shift_hours
                        })

                shortage_val = solver.Value(shortages[(r, d_idx)])
                for _ in range(shortage_val):
                    shift_hours = get_hours_for_slot(w_day, r, slot_counter)
                    slot_counter += 1
                    result_schedule.append({
                        "date": date_str,
                        "formatted_date": formatted_date,
                        "telegram_id": "0",
                        "full_name": "⚠️ ПОТРІБНА ПІДМОГА",
                        "role": r,
                        "shift_hours": shift_hours
                    })

        return result_schedule, "Успішно"

    return None, "Не вдалося сформувати графік."
