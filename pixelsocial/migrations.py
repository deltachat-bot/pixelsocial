"""Database migrations"""

import sqlite3
from pathlib import Path

from deltachat2 import Bot

DATABASE_VERSION = 2


def get_db(path: Path) -> sqlite3.Connection:
    database = sqlite3.connect(path)
    database.row_factory = sqlite3.Row
    return database


def create_version_table(database: sqlite3.Connection) -> None:
    database.execute("""CREATE TABLE IF NOT EXISTS "database" (
        "id" INTEGER NOT NULL,
	"version" INTEGER NOT NULL,
	PRIMARY KEY("id")
        )""")


def set_version(database: sqlite3.Connection, version: int) -> None:
    stmt = "REPLACE INTO database VALUES (?,?)"
    database.execute(stmt, (1, version))


def get_db_version(database: sqlite3.Connection) -> int:
    with database:
        create_version_table(database)
        row = database.execute("SELECT version FROM database").fetchone()
        return row["version"] if row else 0


def run_migrations(bot: Bot, path: Path) -> None:
    if not path.exists():
        database = get_db(path)
        with database:
            create_version_table(database)
            set_version(database, DATABASE_VERSION)
        bot.logger.debug("Database doesn't exists, skipping migrations")
        return

    database = get_db(path)
    try:
        version = get_db_version(database)
        bot.logger.debug(f"Current database version: v{version}")
        for i in range(version + 1, DATABASE_VERSION + 1):
            migration = globals().get(f"migrate{i}")
            assert migration
            bot.logger.info(f"Migrating database: v{i}")
            with database:
                set_version(database, i)
                migration(bot, database)
    finally:
        database.close()


def migrate1(_bot: Bot, database: sqlite3.Connection) -> None:
    database.execute("ALTER TABLE reply ADD COLUMN image VARCHAR")
    database.execute("ALTER TABLE reply ADD COLUMN style INTEGER")


def migrate2(_bot: Bot, database: sqlite3.Connection) -> None:
    database.execute("ALTER TABLE post ADD COLUMN filename VARCHAR")
    database.execute("ALTER TABLE reply ADD COLUMN filename VARCHAR")
