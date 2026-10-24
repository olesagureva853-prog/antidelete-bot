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
        "будут мгновенно пересылаться вам прямо в этот диалог с ботом!"
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
        f"🧹 Автоочистка записей: каждые <b>{config.RETENTION_DAYS} дн.</b>"
    )
    await message.answer(text)


# ========================================================
# Обработка подключения Telegram Business
# ========================================================
@dp.business_connection()
async def on_business_connection(conn: BusinessConnection, bot: Bot):
    user = conn.user
    user_name = f"{user.first_name} {user.last_name or ''}".strip()
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
            is_outgoing=False
        )

        # ========================================================
        # Перехват исчезающих/любых медиа при ответе (Reply)
        # ========================================================
        if message.reply_to_message and config.SAVE_MEDIA_ON_REPLY:
            owner_chat_id = await db.get_owner_chat_id(conn_id)
            if owner_chat_id:
                trigger = config.REPLY_TRIGGER_KEYWORD
                user_text = (message.text or message.caption or "").strip()
                is_trigger_matched = True
                if trigger:
                    is_trigger_matched = (user_text.lower() == trigger.lower()) or user_text.startswith(trigger)

                if is_trigger_matched:
                    replied = message.reply_to_message
                    # Проверяем наличие медиа: сначала в кэше БД, затем напрямую из объекта
                    cached = await db.get_message(chat_id, replied.message_id)
                    r_media_path = None
                    r_media_type = None

                    if cached and cached.get("has_media") and cached.get("media_path") and os.path.exists(cached["media_path"]):
                        r_media_path = cached["media_path"]
                        r_media_type = cached.get("media_type") or "Фото"
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
                            "📥 <b>ПЕРЕХВАЧЕНО ФОТО / МЕДИА ИЗ ОТВЕТА!</b>\n\n"
                            f"👤 <b>От:</b> {user_str}\n"
                            f"💬 <b>Чат:</b> {escaped_chat}\n"
                            f"📎 <b>Тип:</b> {html.escape(r_media_type or 'Вложение')}\n\n"
                            "<i>Медиафайл отправлен вам в бота. Вы можете посмотреть его прямо здесь, не открывая в основном диалоге!</i>"
                        )
                        await send_media_to_chat(bot, owner_chat_id, r_media_path, r_media_type, caption)
                        logger.info(f"Перехвачено медиа из ответа на сообщение {replied.message_id} для {owner_chat_id}")

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
# Сохранение медиа, отправленных напрямую в бота
# ========================================================
@dp.message(F.chat.type == "private", F.photo | F.video | F.voice | F.video_note | F.document)
async def handle_direct_media(message: Message, bot: Bot):
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
