from datetime import datetime

# Пам'ять активних сесій та побажань
active_wish_session = {
    "is_active": False,
    "start_date": None,
    "days_count": None,
    "end_time": None
}

# Словник побажань: {telegram_id: {"off_dates": set(), "shift_prefs": {}, "notes": ""}}
user_wishes = {}

def start_session(start_date_str: str, days_count: int, hours_limit: int = 24):
    global active_wish_session, user_wishes
    end_time = datetime.now() + timedelta(hours=hours_limit) if 'timedelta' in globals() else datetime.now()
    active_wish_session = {
        "is_active": True,
        "start_date": start_date_str,
        "days_count": days_count,
        "end_time": end_time
    }
    user_wishes.clear()

def stop_session():
    global active_wish_session
    active_wish_session["is_active"] = False

def get_session():
    return active_wish_session

def toggle_off_date(user_id: int, date_str: str) -> set:
    uid = str(user_id)
    if uid not in user_wishes:
        user_wishes[uid] = {"off_dates": set(), "shift_prefs": {}, "notes": ""}
    
    off_set = user_wishes[uid]["off_dates"]
    if date_str in off_set:
        off_set.remove(date_str)
    else:
        off_set.add(date_str)
    return off_set

def get_user_wishes(user_id: int):
    return user_wishes.get(str(user_id), {"off_dates": set(), "shift_prefs": {}, "notes": ""})

def get_all_wishes():
    return user_wishes
