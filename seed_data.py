import database

def seed():
    sheet = database.get_sheet()
    
    # 1. Заповнюємо співробітників
    ws_emp = sheet.worksheet("Співробітники")
    fake_employees = [
        ["100001", "Олександр Ткаченко", "waiter", "+380971110001", ""],
        ["100002", "Анна Ковальчук", "hostess", "+380971110002", ""],
        ["100003", "Сергій Лисенко", "delivery_operator", "+380971110003", ""],
        ["100004", "Віталій Мороз", "loader", "+380971110004", ""]
    ]
    for emp in fake_employees:
        ws_emp.append_row(emp)
    
    # 2. Заповнюємо тестовий графік
    ws_sched = sheet.worksheet("Графік")
    fake_shifts = [
        ["2026-10-11", "100001", "Олександр Ткаченко", "waiter", "09:00 - 18:00"],
        ["2026-10-11", "100002", "Анна Ковальчук", "hostess", "10:00 - 19:00"],
        ["2026-10-12", "100003", "Сергій Лисенко", "delivery_operator", "11:00 - 20:00"],
        ["2026-10-12", "100004", "Віталій Мороз", "loader", "08:00 - 17:00"]
    ]
    for shift in fake_shifts:
        ws_sched.append_row(shift)

    print("✅ Тестову базу успішно додано в Google Таблицю!")

if __name__ == "__main__":
    seed()
