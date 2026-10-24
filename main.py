import os
import sys
import html
import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    BusinessConnection,
    BusinessMessagesDeleted,
    FSInputFile
)

import config
import database as db

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("AiogramAntiDelete")

dp = Dispatcher()


def format_user_str(name: str, username: str = None, user_id: int = None) -> str:
    escaped_name = html.escape(name or "Неизвестный")
    parts = [f"<b>{escaped_name}</b>"]
    if username:
        parts.append(f"(@{username})")
    if user_id:
        parts.append(f"[<code>{user_id}</code>]")
    return " ".join(parts)


def get_media_object(message: Message):
    """Определение типа медиафайла и объекта для скачивания."""
    if message.photo:
        return message.photo[-1], "Фото", ".jpg"
    elif message.voice:
        return message.voice, "Голосовое сообщение", ".ogg"
    elif message.video_note:
        return message.video_note, "Видеосообщение (кружочек)", ".mp4"
    elif message.video:
        return message.video, "Видео", ".mp4"
    elif message.audio:
        return message.audio, "Аудиозапись", ".mp3"
    elif message.sticker:
        return message.sticker, "Стикер", ".webp"
    elif message.document:
        ext = os.path.splitext(message.document.file_name or "")[1] or ".dat"
        return message.document, f"Документ ({message.document.file_name or 'файл'})", ext
    return None, None, None


async def download_media(bot: Bot, message: Message, chat_id: int, msg_id: int):
    """Скачивание медиафайла на диск для безопасного хранения."""
    file_obj, media_desc, ext = get_media_object(message)
    if not file_obj or not config.SAVE_MEDIA:
        return False, None, None

    file_size = getattr(file_obj, "file_size", 0) or 0
    if file_size > config.MAX_MEDIA_SIZE_BYTES:
        return False, None, f"{media_desc} (размер превышает лимит {config.MAX_MEDIA_SIZE_MB} МБ)"

    filename = f"{abs(chat_id)}_{msg_id}{ext}"
    filepath = config.MEDIA_DIR / filename
    try:
        await bot.download(file_obj, destination=filepath)
        return True, str(filepath), media_desc
    except Exception as e:
        logger.warning(f"Не удалось загрузить медиа: {e}")
        return False, None, media_desc


async def send_media_to_chat(bot: Bot, chat_id: int, media_path: str, media_type: str, caption: str):
    """Отправка медиафайла пользователю в соответствующем формате."""
    input_file = FSInputFile(media_path)
    m_lower = (media_type or "").lower()
    try:
        if "фото" in m_lower or media_path.endswith((".jpg", ".jpeg", ".png", ".webp")):
            if len(caption) <= 1024:
                await bot.send_photo(chat_id=chat_id, photo=input_file, caption=caption)
            else:
                await bot.send_photo(chat_id=chat_id, photo=input_file)
                await bot.send_message(chat_id=chat_id, text=caption)
        elif "видеосообщение" in m_lower or "кружочек" in m_lower:
            await bot.send_video_note(chat_id=chat_id, video_note=input_file)
            await bot.send_message(chat_id=chat_id, text=caption)
        elif "голосовое" in m_lower or media_path.endswith(".ogg"):
            await bot.send_voice(chat_id=chat_id, voice=input_file, caption=caption[:1024])
        elif "видео" in m_lower or media_path.endswith((".mp4", ".mov")):
            await bot.send_video(chat_id=chat_id, video=input_file, caption=caption[:1024])
        else:
            await bot.send_document(chat_id=chat_id, document=input_file, caption=caption[:1024])
    except Exception as e:
        logger.warning(f"Ошибка при специализированной отправке медиа, пробуем как документ: {e}")
        try:
            await bot.send_document(chat_id=chat_id, document=input_file, caption=caption[:1024])
        except Exception as err2:
            logger.error(f"Не удалось отправить файл: {err2}")


# ========================================================
# Базовые команды бота
# ========================================================
@dp.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    if message.from_user:
        u = message.from_user
        full_name = f"{u.first_name} {u.last_name or ''}".strip()
        await db.track_user(u.id, full_name, u.username)

    bot_info = await bot.get_me()
    text = (
        "👋 <b>Привет! Я бот для сохранения удалённых и отредактированных сообщений.</b>\n\n"
        "Я работаю на базе <b>aiogram 3</b> через официальный протокол <b>Telegram Business</b> (Bot API 7.2+).\n\n"
        "📌 <b>Как подключить бота к вашему аккаунту:</b>\n"
        "1️⃣ Откройте <b>Настройки Telegram</b> -> <b>Telegram для бизнеса</b> <i>(требуется Telegram Premium)</i>.\n"
        "2️⃣ Выберите раздел <b>Чат-боты</b> (Chatbots).\n"
        f"3️⃣ Введите имя этого бота: <code>@{bot_info.username}</code> и добавьте его.\n"
        "4️⃣ Выберите чаты, за которыми бот будет наблюдать (все личные чаты).\n\n"
        "✅ После этого любые сообщения, которые удалят или отредактируют ваши собеседники, "
        "будут мгновенно пересылаться вам прямо в этот диалог с ботом!\n\n"
        "🛠 <b>Команды управления:</b>\n"
        "• /status — статистика базы данных и статус подключений\n"
        "• /users — список пользователей и подключений (только для владельца)\n"
        "• /backup — скачать резервную копию базы данных"
    )
    await message.answer(text)


@dp.message(Command("status"))
async def cmd_status(message: Message):
    # Проверка прав: доступ только владельцу
    sender_id = message.from_user.id if message.from_user else 0
    if not await db.is_owner(sender_id):
        await message.answer("⛔ <b>Доступ запрещён.</b> Эта команда доступна только владельцу бота.")
        return

    stats = await db.get_stats()
    text = (
        "📊 <b>Статус бота:</b>\n\n"
        f"👤 Активных бизнес-подключений: <b>{stats['active_connections']}</b>\n"
        f"💾 Сообщений в кэше: <b>{stats['total_messages']}</b>\n"
        f"🗑 Зафиксировано удалений: <b>{stats['deleted_messages']}</b>\n"
        f"📁 Папка медиа: <code>{config.MEDIA_DIR}</code>\n"
        f"🧹 Автоочистка записей: каждые <b>{config.RETENTION_DAYS} дн.</b>\n\n"
        "💡 <i>Используйте /backup, чтобы скачать резервную копию всей базы данных.</i>"
    )
    await message.answer(text)


@dp.message(Command("backup"))
async def cmd_backup(message: Message, bot: Bot):
    # Создание и отправка резервной копии базы данных
    sender_id = message.from_user.id if message.from_user else 0
    if not await db.is_owner(sender_id):
        await message.answer("⛔ <b>Доступ запрещён.</b> Эта команда доступна только владельцу бота.")
        return

    backup_path = await db.create_backup()
    if not backup_path or not os.path.exists(backup_path):
        await message.answer("⚠️ База данных пока пуста или не инициализирована.")
        return

    stats = await db.get_stats()
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    caption = (
        "📦 <b>РЕЗЕРВНАЯ КОПИЯ БАЗЫ ДАННЫХ</b>\n\n"
        f"📅 <b>Дата:</b> {now_str}\n"
        f"💾 <b>Сообщений:</b> <b>{stats['total_messages']}</b>\n"
        f"🗑 <b>Удалений:</b> <b>{stats['deleted_messages']}</b>\n"
        f"👤 <b>Подключений:</b> <b>{stats['active_connections']}</b>\n\n"
        "💡 <b>Как восстановить данные:</b>\n"
        "<i>Если после обновления Git или перезапуска база сбросилась, "
        "просто отправьте этот файл боту в этот чат (можно с подписью <code>/restore</code>), "
        "и бот мгновенно восстановит всю историю!</i>"
    )

    doc = FSInputFile(backup_path, filename=f"messages_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db")
    await bot.send_document(chat_id=message.chat.id, document=doc, caption=caption)


@dp.message(Command("users"))
async def cmd_users(message: Message):
    # Просмотр всех пользователей бота (только для владельца)
    sender_id = message.from_user.id if message.from_user else 0
    if not await db.is_owner(sender_id):
        await message.answer("⛔ <b>Доступ запрещён.</b> Эта команда доступна только владельцу бота.")
        return

    data = await db.get_bot_users_list()
    b_users = data["business_users"]
    d_users = data["direct_users"]

    lines = ["👥 <b>СПИСОК ПОЛЬЗОВАТЕЛЕЙ БОТА</b>\n"]

    # 1. Бизнес-подключения (кто подключил бота к своим личным чатам)
    lines.append(f"💼 <b>Подключённые бизнес-аккаунты ({len(b_users)}):</b>")
    if b_users:
        for idx, u in enumerate(b_users, 1):
            name = html.escape(u.get("user_name") or "Пользователь")
            username = f"(@{u['user_username']})" if u.get("user_username") else ""
            status = "🟢 Активно" if u.get("is_enabled") else "🔴 Отключено"
            date = u.get("updated_at") or ""
            lines.append(f"{idx}. <b>{name}</b> {username} [<code>{u['user_id']}</code>] — {status} ({date})")
    else:
        lines.append("<i>Пока нет активных бизнес-подключений.</i>")

    lines.append("")

    # 2. Пользователи, которые писали боту в ЛС
    lines.append(f"💬 <b>Пользователи в диалоге с ботом ({len(d_users)}):</b>")
    if d_users:
        for idx, u in enumerate(d_users[:30], 1):
            name = html.escape(u.get("full_name") or "Пользователь")
            username = f"(@{u['username']})" if u.get("username") else ""
            date = u.get("last_seen") or ""
            lines.append(f"{idx}. <b>{name}</b> {username} [<code>{u['user_id']}</code>] — {date}")
        if len(d_users) > 30:
            lines.append(f"<i>...и ещё {len(d_users) - 30} пользователей.</i>")
    else:
        lines.append("<i>Пока никто не писал боту напрямую.</i>")

    await message.answer("\n".join(lines))


# ========================================================
# Обработка подключения Telegram Business
# ========================================================
@dp.business_connection()
async def on_business_connection(conn: BusinessConnection, bot: Bot):
    user = conn.user
    user_name = f"{user.first_name} {user.last_name or ''}".strip()
    await db.track_user(user.id, user_name, user.username)
    await db.save_connection(
        connection_id=conn.id,
        user_id=user.id,
        user_chat_id=conn.user_chat_id,
        user_name=user_name,
        user_username=user.username,
        is_enabled=conn.is_enabled
    )

    if conn.is_enabled:
        logger.info(f"Активировано бизнес-подключение для пользователя {user_name} ({user.id})")
        msg = (
            "🎉 <b>Бизнес-подключение успешно активировано!</b>\n\n"
            "Бот начал отслеживание ваших личных диалогов. Все удалённые тексты, "
            "фото, видео, голосовые сообщения и кружочки будут приходить вам сюда."
        )
        try:
            await bot.send_message(chat_id=conn.user_chat_id, text=msg)
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление о подключении: {e}")
    else:
        logger.info(f"Бизнес-подключение отключено для пользователя {user_name} ({user.id})")


# ========================================================
# Обработка сообщений Telegram Business (кэширование и перехват)
# ========================================================
@dp.business_message()
async def on_business_message(message: Message, bot: Bot):
    try:
        msg_id = message.message_id
        chat_id = message.chat.id
        conn_id = message.business_connection_id

        sender = message.from_user
        sender_id = sender.id if sender else None
        sender_name = f"{sender.first_name} {sender.last_name or ''}".strip() if sender else "Неизвестный"
        sender_username = sender.username if sender else None

        chat_title = message.chat.full_name or message.chat.title or f"Чат {chat_id}"
        text = message.text or message.caption or ""

        # Определяем, отправлено ли сообщение владельцем (исходящее)
        conn = await db.get_connection(conn_id)
        owner_id = config.OWNER_ID or (conn.get("user_id") if conn else None)
        is_outgoing = bool(sender_id and owner_id and sender_id == owner_id)

        # Проверяем, является ли медиа сгорающим / исчезающим
        is_disappearing = bool(
            getattr(message, 'has_media_spoiler', False) or 
            getattr(message, 'has_protected_content', False)
        )

        # Кэшируем текущее сообщение и его медиа
        has_media, media_path, media_type = await download_media(bot, message, chat_id, msg_id)

        await db.save_message(
            msg_id=msg_id,
            chat_id=chat_id,
            connection_id=conn_id,
            sender_id=sender_id,
            sender_name=sender_name,
            sender_username=sender_username,
            chat_title=chat_title,
            text=text,
            has_media=has_media,
            media_path=media_path,
            media_type=media_type,
            is_outgoing=is_outgoing,
            is_disappearing=is_disappearing
        )

        # ========================================================
        # Перехват ТОЛЬКО исчезающих/сгорающих фото при ответе (Reply)
        # ========================================================
        if message.reply_to_message and config.SAVE_MEDIA_ON_REPLY and is_outgoing:
            owner_chat_id = await db.get_owner_chat_id(conn_id)
            if owner_chat_id:
                replied = message.reply_to_message
                user_text = (message.text or message.caption or "").strip()

                # Проверяем: это сгорающее / исчезающее фото?
                is_disappearing_media = bool(
                    getattr(replied, 'has_media_spoiler', False) or 
                    getattr(replied, 'has_protected_content', False)
                )

                cached = await db.get_message(chat_id, replied.message_id)
                if cached and cached.get("is_disappearing"):
                    is_disappearing_media = True

                # Принудительное сохранение обычного фото по спецкоманде /save или сохрани
                forced_save = user_text.lower() in ("/save", "!save", "сохрани", "save")

                # Если это ОБЫЧНАЯ картинка (не сгорающее фото) и нет явной команды — ПРОПУСКАЕМ!
                if not is_disappearing_media and not forced_save:
                    pass
                else:
                    r_media_path = None
                    r_media_type = None

                    if cached and cached.get("has_media") and cached.get("media_path") and os.path.exists(cached["media_path"]):
                        r_media_path = cached["media_path"]
                        r_media_type = cached.get("media_type") or "Сгорающее фото"
                    else:
                        has_m, m_path, m_type = await download_media(bot, replied, chat_id, replied.message_id)
                        if has_m and m_path:
                            r_media_path = m_path
                            r_media_type = m_type

                    if r_media_path and os.path.exists(r_media_path):
                        r_sender = replied.from_user
                        r_name = f"{r_sender.first_name} {r_sender.last_name or ''}".strip() if r_sender else "Собеседник"
                        r_uname = r_sender.username if r_sender else None
                        r_id = r_sender.id if r_sender else None
                        user_str = format_user_str(r_name, r_uname, r_id)
                        escaped_chat = html.escape(chat_title)

                        caption = (
                            "🔥 <b>ПЕРЕХВАЧЕНО СГОРАЮЩЕЕ ФОТО!</b>\n\n"
                            f"👤 <b>От:</b> {user_str}\n"
                            f"💬 <b>Чат:</b> {escaped_chat}\n"
                            f"📎 <b>Тип:</b> {html.escape(r_media_type or 'Исчезающее фото')}\n\n"
                            "<i>Медиафайл успешно сохранён. Вы можете посмотреть его прямо здесь, не открывая в основном диалоге!</i>"
                        )
                        await send_media_to_chat(bot, owner_chat_id, r_media_path, r_media_type, caption)
                        logger.info(f"Перехвачено сгорающее медиа из ответа на сообщение {replied.message_id}")

    except Exception as e:
        logger.error(f"Ошибка при обработке business_message: {e}")


# ========================================================
# Обработка редактирования сообщений
# ========================================================
@dp.edited_business_message()
async def on_edited_business_message(message: Message, bot: Bot):
    if not config.LOG_EDITED:
        return

    try:
        msg_id = message.message_id
        chat_id = message.chat.id
        new_text = message.text or message.caption or ""

        saved = await db.get_message(chat_id, msg_id)
        if not saved:
            return

        old_text = saved.get("text") or ""
        if old_text == new_text or not new_text:
            return

        # Проверка: если сообщение отредактировал сам владелец — обновляем в БД, но НЕ оповещаем
        conn = await db.get_connection(message.business_connection_id)
        owner_id = config.OWNER_ID or (conn.get("user_id") if conn else None)
        sender_id = message.from_user.id if message.from_user else None

        if saved.get("is_outgoing") or (sender_id and owner_id and sender_id == owner_id):
            await db.update_message_text(saved["id"], new_text)
            return

        owner_chat_id = await db.get_owner_chat_id(message.business_connection_id)
        if not owner_chat_id:
            return

        sender_name = saved.get("sender_name") or "Неизвестный"
        sender_username = saved.get("sender_username")
        sender_id = saved.get("sender_id")
        chat_title = saved.get("chat_title") or f"Чат {chat_id}"

        user_str = format_user_str(sender_name, sender_username, sender_id)
        escaped_chat = html.escape(chat_title)
        escaped_old = html.escape(old_text[:2000]) if old_text else "<i>(без текста)</i>"
        escaped_new = html.escape(new_text[:2000])

        report = (
            "✏️ <b>ОТРЕДАКТИРОВАНО СООБЩЕНИЕ</b>\n\n"
            f"👤 <b>От:</b> {user_str}\n"
            f"💬 <b>Чат:</b> {escaped_chat}\n"
            f"⏰ <b>Время:</b> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n"
            f"🔴 <b>Было:</b>\n<blockquote>{escaped_old}</blockquote>\n\n"
            f"🟢 <b>Стало:</b>\n<blockquote>{escaped_new}</blockquote>"
        )

        await bot.send_message(chat_id=owner_chat_id, text=report)
        await db.update_message_text(saved["id"], new_text)
        logger.info(f"Зафиксировано редактирование в чате {chat_title}")
    except Exception as e:
        logger.error(f"Ошибка при обработке редактирования: {e}")


# ========================================================
# Обработка удалённых сообщений
# ========================================================
@dp.deleted_business_messages()
async def on_deleted_business_messages(event: BusinessMessagesDeleted, bot: Bot):
    try:
        owner_chat_id = await db.get_owner_chat_id(event.business_connection_id)
        if not owner_chat_id:
            logger.warning(f"Не найден владелец для подключения {event.business_connection_id}")
            return

        chat_id = event.chat.id

        for msg_id in event.message_ids:
            record = await db.get_message(chat_id, msg_id)
            if not record or record.get("is_deleted"):
                continue

            # Проверка: если сообщение было отправлено самим владельцем — не оповещаем
            if record.get("is_outgoing"):
                logger.info(f"Сообщение {msg_id} удалено владельцем бота — уведомление пропущено.")
                continue

            await db.mark_message_deleted(record["id"])

            sender_name = record.get("sender_name") or "Неизвестный"
            sender_username = record.get("sender_username")
            sender_id = record.get("sender_id")
            chat_title = record.get("chat_title") or "Личный чат"
            sent_at = record.get("created_at") or "Неизвестно"
            raw_text = record.get("text") or ""
            has_media = bool(record.get("has_media"))
            media_path = record.get("media_path")
            media_type = record.get("media_type") or "Вложение"

            user_str = format_user_str(sender_name, sender_username, sender_id)
            escaped_chat = html.escape(chat_title)
            escaped_text = html.escape(raw_text[:3500]) if raw_text else "<i>(текст отсутствовал)</i>"

            media_line = f"\n📎 <b>Вложение:</b> {html.escape(media_type)}" if has_media else ""

            report = (
                "🗑 <b>УДАЛЕНО СООБЩЕНИЕ!</b>\n\n"
                f"👤 <b>От:</b> {user_str}\n"
                f"💬 <b>Чат:</b> {escaped_chat}\n"
                f"📅 <b>Отправлено:</b> {sent_at}\n"
                f"⏰ <b>Удалено:</b> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
                f"{media_line}\n\n"
                f"📝 <b>Содержимое:</b>\n<blockquote>{escaped_text}</blockquote>"
            )

            # Отправляем владельцу сохранённый файл (если был)
            if has_media and media_path and os.path.exists(media_path):
                await send_media_to_chat(bot, owner_chat_id, media_path, media_type, report)
            else:
                await bot.send_message(chat_id=owner_chat_id, text=report)

            logger.info(f"Удалённое сообщение от {sender_name} из «{chat_title}» отправлено владельцу.")
    except Exception as e:
        logger.error(f"Ошибка при обработке удаления business_messages: {e}")


# ========================================================
# Сохранение медиа или восстановление базы из бэкапа
# ========================================================
@dp.message(F.chat.type == "private", F.document)
async def handle_document_or_restore(message: Message, bot: Bot):
    if message.from_user:
        u = message.from_user
        await db.track_user(u.id, f"{u.first_name} {u.last_name or ''}".strip(), u.username)

    sender_id = message.from_user.id if message.from_user else 0
    doc_name = (message.document.file_name or "").lower()
    caption = (message.caption or "").lower()

    # Проверка: является ли присланный файл резервной копией базы
    is_backup_file = doc_name.endswith(".db") or "/restore" in caption

    if is_backup_file and await db.is_owner(sender_id):
        temp_path = config.DATA_DIR / "temp_restore.db"
        try:
            await bot.download(message.document, destination=temp_path)
            success = await db.restore_from_file(str(temp_path))
            if temp_path.exists():
                temp_path.unlink()

            if success:
                stats = await db.get_stats()
                text = (
                    "✅ <b>БАЗА ДАННЫХ УСПЕШНО ВОССТАНОВЛЕНА!</b>\n\n"
                    "Все ваши данные и история возвращены:\n"
                    f"• Сообщений в базе: <b>{stats['total_messages']}</b>\n"
                    f"• Зафиксировано удалений: <b>{stats['deleted_messages']}</b>\n"
                    f"• Активных подключений: <b>{stats['active_connections']}</b>"
                )
                await message.reply(text)
                logger.info(f"База данных успешно восстановлена владельцем {sender_id}")
                return
            else:
                await message.reply("❌ <b>Ошибка:</b> файл повреждён или не является корректной базой SQLite.")
                return
        except Exception as e:
            logger.error(f"Ошибка при восстановлении базы данных: {e}")
            await message.reply(f"❌ Ошибка восстановления: {e}")
            return

    # Обычное сохранение документа
    has_media, media_path, media_type = await download_media(bot, message, message.chat.id, message.message_id)
    if has_media and media_path:
        reply_caption = f"✅ <b>{media_type} сохранено в постоянное хранилище!</b>"
        await send_media_to_chat(bot, message.chat.id, media_path, media_type, reply_caption)


@dp.message(F.chat.type == "private", F.photo | F.video | F.voice | F.video_note)
async def handle_direct_media(message: Message, bot: Bot):
    if message.from_user:
        u = message.from_user
        await db.track_user(u.id, f"{u.first_name} {u.last_name or ''}".strip(), u.username)

    has_media, media_path, media_type = await download_media(bot, message, message.chat.id, message.message_id)
    if has_media and media_path:
        caption = f"✅ <b>{media_type} сохранено в постоянное хранилище!</b>"
        await send_media_to_chat(bot, message.chat.id, media_path, media_type, caption)


# ========================================================
# Фоновая автоочистка старых сообщений
# ========================================================
async def cleanup_loop():
    while True:
        await asyncio.sleep(86400)
        try:
            cleaned = await db.cleanup_old_records(config.RETENTION_DAYS)
            if cleaned > 0:
                logger.info(f"Автоочистка: удалено {cleaned} старых записей.")
        except Exception as e:
            logger.error(f"Ошибка в цикле очистки: {e}")


async def main():
    try:
        config.validate_config()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    await db.init_db()
    logger.info("База данных SQLite успешно инициализирована.")

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    try:
        bot_info = await bot.get_me()
    except Exception as e:
        logger.error(f"Не удалось подключиться к Telegram с указанным токеном: {e}")
        await bot.session.close()
        sys.exit(1)

    print("\n" + "=" * 55)
    print("🤖 AIOGRAM ANTI-DELETE BOT ЗАПУЩЕН!")
    print(f"👤 Бот: @{bot_info.username} ({bot_info.first_name})")
    print(f"💾 Кэш медиа: {'Включен (до ' + str(config.MAX_MEDIA_SIZE_MB) + ' МБ)' if config.SAVE_MEDIA else 'Выключен'}")
    print(f"✏️  Лог редактирований: {'Включен' if config.LOG_EDITED else 'Выключен'}")
    print(f"🧹 Автоочистка: каждые {config.RETENTION_DAYS} дн.")
    print("=" * 55 + "\n")

    asyncio.create_task(cleanup_loop())

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nБот остановлен пользователем.")
