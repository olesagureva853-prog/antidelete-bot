import os
import shutil
import sqlite3
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from config import DB_PATH


async def init_db():
    """Инициализация базы данных SQLite."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")

        # Таблица подключений Telegram Business
        await db.execute("""
            CREATE TABLE IF NOT EXISTS business_connections (
                connection_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                user_chat_id INTEGER NOT NULL,
                user_name TEXT,
                user_username TEXT,
                is_enabled INTEGER DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Таблица сохраненных сообщений
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                connection_id TEXT,
                msg_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                chat_title TEXT,
                sender_id INTEGER,
                sender_name TEXT,
                sender_username TEXT,
                text TEXT,
                has_media INTEGER DEFAULT 0,
                media_path TEXT,
                media_type TEXT,
                is_outgoing INTEGER DEFAULT 0,
                is_deleted INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await db.execute("CREATE INDEX IF NOT EXISTS idx_chat_msg ON messages(chat_id, msg_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON messages(created_at);")
        await db.commit()


async def save_connection(
    connection_id: str,
    user_id: int,
    user_chat_id: int,
    user_name: str,
    user_username: Optional[str],
    is_enabled: bool
):
    """Сохранение бизнес-подключения аккаунта к боту."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO business_connections (
                connection_id, user_id, user_chat_id, user_name, user_username, is_enabled, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(connection_id) DO UPDATE SET
                is_enabled = excluded.is_enabled,
                updated_at = excluded.updated_at;
        """, (connection_id, user_id, user_chat_id, user_name, user_username, 1 if is_enabled else 0))
        await db.commit()


async def get_owner_chat_id(connection_id: str) -> Optional[int]:
    """Получение chat_id владельца подключения для отправки уведомлений."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT user_chat_id FROM business_connections WHERE connection_id = ? LIMIT 1",
            (connection_id,)
        )
        row = await cursor.fetchone()
        if row:
            return row["user_chat_id"]
        return None


async def get_connection(connection_id: str) -> Optional[Dict[str, Any]]:
    """Получение данных бизнес-подключения."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM business_connections WHERE connection_id = ? LIMIT 1",
            (connection_id,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def save_message(
    msg_id: int,
    chat_id: int,
    connection_id: Optional[str],
    sender_id: Optional[int],
    sender_name: str,
    sender_username: Optional[str],
    chat_title: str,
    text: str,
    has_media: bool = False,
    media_path: Optional[str] = None,
    media_type: Optional[str] = None,
    is_outgoing: bool = False
):
    """Сохранение сообщения в базу данных."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO messages (
                connection_id, msg_id, chat_id, chat_title, sender_id, sender_name,
                sender_username, text, has_media, media_path, media_type,
                is_outgoing, is_deleted, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, datetime('now', 'localtime'))
        """, (
            connection_id,
            msg_id,
            chat_id,
            chat_title,
            sender_id,
            sender_name,
            sender_username,
            text,
            1 if has_media else 0,
            media_path,
            media_type,
            1 if is_outgoing else 0
        ))
        await db.commit()


async def get_message(chat_id: int, msg_id: int) -> Optional[Dict[str, Any]]:
    """Поиск сообщения по chat_id и msg_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM messages
            WHERE chat_id = ? AND msg_id = ?
            ORDER BY id DESC LIMIT 1
        """, (chat_id, msg_id))
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def mark_message_deleted(row_id: int):
    """Пометить сообщение как удаленное."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE messages SET is_deleted = 1 WHERE id = ?", (row_id,))
        await db.commit()


async def update_message_text(row_id: int, new_text: str):
    """Обновление текста сообщения при редактировании."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE messages SET text = ? WHERE id = ?", (new_text, row_id))
        await db.commit()


async def cleanup_old_records(retention_days: int) -> int:
    """Удаление старых сообщений и кэшированных файлов с диска."""
    threshold = (datetime.now() - timedelta(days=retention_days)).strftime('%Y-%m-%d %H:%M:%S')
    deleted_count = 0
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, media_path FROM messages WHERE created_at < ?",
            (threshold,)
        )
        rows = await cursor.fetchall()
        for row in rows:
            media_path = row["media_path"]
            if media_path and os.path.exists(media_path):
                try:
                    os.remove(media_path)
                except OSError:
                    pass
            deleted_count += 1

        await db.execute("DELETE FROM messages WHERE created_at < ?", (threshold,))
        await db.commit()
    return deleted_count


async def get_stats() -> Dict[str, Any]:
    """Получение статистики базы данных."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM messages") as cursor:
            total_msgs = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM messages WHERE is_deleted = 1") as cursor:
            deleted_msgs = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM business_connections WHERE is_enabled = 1") as cursor:
            active_conns = (await cursor.fetchone())[0]
        return {
            "total_messages": total_msgs,
            "deleted_messages": deleted_msgs,
            "active_connections": active_conns
        }


async def is_owner(user_id: int) -> bool:
    """Проверка, является ли пользователь владельцем бота."""
    import config
    if config.OWNER_ID and user_id == config.OWNER_ID:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM business_connections WHERE user_id = ? AND is_enabled = 1 LIMIT 1",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def create_backup() -> Optional[str]:
    """Сброс WAL-журнала и подготовка файла базы данных к отправке."""
    if not DB_PATH.exists():
        return None
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            await db.commit()
    except Exception:
        pass
    return str(DB_PATH)


async def restore_from_file(new_db_path: str) -> bool:
    """Проверка целостности и восстановление базы данных из присланного файла."""
    try:
        # Проверяем целостность файла через sqlite3
        con = sqlite3.connect(new_db_path)
        cur = con.cursor()
        cur.execute("PRAGMA integrity_check;")
        res = cur.fetchone()
        con.close()
        if not res or res[0] != "ok":
            return False
    except Exception:
        return False

    try:
        # Заменяем текущую базу данных
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(new_db_path, DB_PATH)

        # Удаляем временные WAL/SHM файлы старой сессии, если они есть
        wal_file = DB_PATH.with_suffix(".db-wal")
        shm_file = DB_PATH.with_suffix(".db-shm")
        if wal_file.exists():
            wal_file.unlink()
        if shm_file.exists():
            shm_file.unlink()

        # Повторно инициализируем индексы и таблицы
        await init_db()
        return True
    except Exception:
        return False
