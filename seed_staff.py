import database

# Список українських імен для генерації
FIRST_NAMES = ["Олександр", "Дмитро", "Максим", "Артем", "Богдан", "Ярослав", "Андрій", "Денис", "Анна", "Софія", "Анастасія", "Вікторія", "Дарина", "Юлія", "Катерина", "Олена"]
LAST_NAMES = ["Коваль", "Шевченко", "Бондаренко", "Ткаченко", "Кравченко", "Ковальчук", "Олійник", "Шевчук", "Поліщук", "Лисенко", "Руденко", "Мороз", "Мельник", "Бойко"]

def generate_employees():
    employees = []
    emp_id = 1001
    
    roles_count = [
        ("waiter", 17),
        ("hostess", 5),
        ("delivery_operator", 2),
        ("loader", 4)
    ]
    
    import random
    fn_idx = 0
    ln_idx = 0

    for role_code, count in roles_count:
        for i in range(count):
            first_name = FIRST_NAMES[fn_idx % len(FIRST_NAMES)]
            last_name = LAST_NAMES[ln_idx % len(LAST_NAMES)]
            full_name = f"{first_name} {last_name}"
            
            fn_idx += 1
            ln_idx += 3
            
            phone = f"+38097{random.randint(1000000, 9999999)}"
            employees.append([str(emp_id), full_name, role_code, phone, ""])
            emp_id += 1
            
    return employees

def seed():
    print("⏳ Підключення до Google Таблиці...")
    sheet = database.get_sheet()
    ws_emp = sheet.worksheet("Співробітники")
    
    employees = generate_employees()
    
    print(f"📥 Додавання {len(employees)} співробітників у Google Таблицю...")
    for emp in employees:
        ws_emp.append_row(emp)
        print(f"  • Додано: {emp[1]} ({emp[2]}) — ID: {emp[0]}")
        
    print("\n✅ Усі 28 співробітників успішно додані в базу!")

if __name__ == "__main__":
    seed()
