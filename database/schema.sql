
-- Таблиця для збереження дій користувачів
CREATE TABLE IF NOT EXISTS user_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,                -- ID користувача
    username TEXT NOT NULL,                  -- Ім'я користувача
    action TEXT NOT NULL,                    -- Опис дії
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP -- Час виконання дії
);

-- Приклад існуючої таблиці користувачів, якщо її ще немає
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,                  -- ID користувача
    username TEXT NOT NULL,                  -- Ім'я користувача
    join_date DATETIME DEFAULT CURRENT_TIMESTAMP -- Дата приєднання
);