



import sqlite3
import logging
import os

logger = logging.getLogger('bot')

class DatabaseManager:
    def __init__(self, db_path="database/bot.db", schema_path="database/schema.sql"):
        self.db_path = db_path
        self.schema_path = schema_path

        # Переконайтеся, що папка для бази даних існує
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # Ініціалізація бази даних, якщо файл не існує
        if not os.path.exists(self.db_path):
            self.initialize_database()

    def connect(self):
        """Create a connection to the SQLite database."""
        return sqlite3.connect(self.db_path)

    def initialize_database(self):
        """Initialize the database using the schema file."""
        logger.info(f"Initializing the database at {self.db_path}...")
        try:
            with self.connect() as conn:
                with open(self.schema_path, "r", encoding="utf-8") as schema_file:
                    conn.executescript(schema_file.read())
            logger.info("Database initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize the database: {e}")

    def add_user_action(self, user_id, username, action):
        """Додати дію користувача до бази даних."""
        logger.info(f"Запис дії користувача {username}: {action}")
        try:
            self.execute_non_query(
                "INSERT INTO user_actions (user_id, username, action) VALUES (?, ?, ?)",
                (user_id, username, action)
            )
        except Exception as e:
            logger.error(f"Failed to add user action: {e}")

    def execute_query(self, query, params=()):
        """Execute a query and fetch all results."""
        try:
            with self.connect() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Query failed: {query} | Error: {e}")
            return None

    def execute_non_query(self, query, params=()):
        """Execute a query without returning results (e.g., INSERT, UPDATE, DELETE)."""
        try:
            with self.connect() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
        except Exception as e:
            logger.error(f"Non-query execution failed: {query} | Error: {e}")

    def add_server(self, server_id, server_name):
        """Add a new server to the database."""
        logger.info(f"Adding server {server_name} with ID {server_id}.")
        self.execute_non_query(
            "INSERT INTO servers (id, name) VALUES (?, ?)", (server_id, server_name)
        )

    def get_servers(self):
        """Retrieve all servers from the database."""
        return self.execute_query("SELECT * FROM servers")

    def add_user(self, user_id, username):
        """Add a new user to the database."""
        logger.info(f"Adding user {username} with ID {user_id}.")
        self.execute_non_query(
            "INSERT INTO users (id, username) VALUES (?, ?)", (user_id, username)
        )

    def get_users(self):
        """Retrieve all users from the database."""
        return self.execute_query("SELECT * FROM users")

    def get_user(self, user_id):
        """Retrieve a specific user by ID."""
        return self.execute_query("SELECT * FROM users WHERE id = ?", (user_id,))

# Example usage:
# db = DatabaseManager()
# db.add_server(12345, "Test Server")
# print(db.get_servers())
# db.add_user(67890, "Test User")
# print(db.get_users())

