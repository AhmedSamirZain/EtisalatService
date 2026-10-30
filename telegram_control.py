"""Admin-managed Telegram access and polling for the Streamlit-hosted bot."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import time
from datetime import datetime
from contextlib import closing
from pathlib import Path
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)


MAX_TARGET_MIX = 1_000_000
(TARGET, CONSENT, OWNER_EMAIL, OWNER_PASSWORD, CHILD_EMAIL, CHILD_PASSWORD, CONFIRM_RUN) = range(7)

BUTTON_RUN = "▶️ تشغيل"
BUTTON_STATUS = "📊 الحالة"
BUTTON_STOP = "⏹ إيقاف"
BUTTON_CANCEL = "❌ إلغاء"
BUTTON_HELP = "ℹ️ مساعدة"
BUTTON_HOME = "🏠 البداية"
BUTTON_ADMIN = "👥 لوحة الإدارة"

DEFAULT_BOT_SETTINGS = {
    "plan_retry_minutes": 4,
    "plan_retry_limit": 3,
    "transfer_retry_seconds": 3,
    "transfer_retry_limit": 3,
    "migration_settle_seconds": 60,
    "post_transfer_wait_seconds": 3,
    "status_refresh_seconds": 5,
}
PLAN_RETRY_MINUTE_OPTIONS = (1, 2, 3, 4, 5, 10, 15)
PLAN_RETRY_LIMIT_OPTIONS = (1, 3, 5, 10, 0)  # 0 means unlimited retries.
TRANSFER_RETRY_SECOND_OPTIONS = (3, 5, 10, 20, 30, 60)
TRANSFER_RETRY_LIMIT_OPTIONS = (1, 3, 5, 10, 0)  # 0 means unlimited retries.
MIGRATION_SETTLE_OPTIONS = (0, 30, 60, 90, 120)
POST_TRANSFER_WAIT_OPTIONS = (0, 1, 3, 5, 10)
STATUS_REFRESH_OPTIONS = (3, 5, 10, 15)


class TelegramControlBot:
    """Run Telegram long polling and collect per-run credentials in memory only."""

    def __init__(self):
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._token: str | None = None
        self._admin_id: int | None = None
        self._runner: Any = None
        self._error: str | None = None
        self._active_user_id: int | None = None
        self._live_status_target: dict[str, int] | None = None
        self._live_status_job = None
        self._users_db = Path(__file__).resolve().parent / ".streamlit" / "authorized_users.sqlite3"
        try:
            self._init_users_db()
        except (OSError, sqlite3.Error):
            self._users_db = Path(tempfile.gettempdir()) / "etisalatservice_authorized_users.sqlite3"
            self._init_users_db()

    def configure(self, token: str, admin_id: int, runner: Any):
        with self._lock:
            if self.is_running() and (token != self._token or admin_id != self._admin_id):
                self._error = "Telegram settings changed; restart the Streamlit app to apply them."
                return
            self._token = token
            self._admin_id = admin_id
            self._runner = runner
            if hasattr(runner, "set_completion_callback"):
                runner.set_completion_callback(self._activation_completed)
            if hasattr(runner, "set_plan_retry_settings_provider"):
                runner.set_plan_retry_settings_provider(self._plan_retry_settings)

    def _init_users_db(self):
        self._users_db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._users_db)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS authorized_users ("
                "user_id INTEGER PRIMARY KEY, added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS user_quotas ("
                "user_id INTEGER PRIMARY KEY, granted_total INTEGER NOT NULL DEFAULT 0, "
                "remaining INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, "
                "successes INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, "
                "stopped INTEGER NOT NULL DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS activations ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, target INTEGER NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'running', started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "finished_at TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS bot_settings ("
                "setting_key TEXT PRIMARY KEY, setting_value INTEGER NOT NULL)"
            )
            db.executemany(
                "INSERT OR IGNORE INTO bot_settings(setting_key, setting_value) VALUES (?, ?)",
                DEFAULT_BOT_SETTINGS.items(),
            )
            db.execute(
                "INSERT OR IGNORE INTO user_quotas(user_id) SELECT user_id FROM authorized_users"
            )
            db.execute(
                "UPDATE user_quotas SET stopped = stopped + ("
                "SELECT COUNT(*) FROM activations WHERE activations.user_id = user_quotas.user_id "
                "AND activations.status = 'running') "
                "WHERE EXISTS (SELECT 1 FROM activations WHERE activations.user_id = user_quotas.user_id "
                "AND activations.status = 'running')"
            )
            db.execute(
                "UPDATE activations SET status = 'stopped', finished_at = CURRENT_TIMESTAMP "
                "WHERE status = 'running'"
            )

    def _bot_settings(self) -> dict[str, int]:
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            rows = db.execute("SELECT setting_key, setting_value FROM bot_settings").fetchall()
        settings = dict(DEFAULT_BOT_SETTINGS)
        settings.update({str(key): int(value) for key, value in rows})
        return settings

    def _plan_retry_settings(self) -> dict[str, int]:
        settings = self._bot_settings()
        return {
            "plan_retry_seconds": settings["plan_retry_minutes"] * 60,
            "plan_retry_limit": settings["plan_retry_limit"],
            "transfer_retry_seconds": settings["transfer_retry_seconds"],
            "transfer_retry_limit": settings["transfer_retry_limit"],
            "migration_settle_seconds": settings["migration_settle_seconds"],
            "post_transfer_wait_seconds": settings["post_transfer_wait_seconds"],
        }

    def _set_bot_setting(self, key: str, value: int) -> bool:
        allowed = {
            "plan_retry_minutes": PLAN_RETRY_MINUTE_OPTIONS,
            "plan_retry_limit": PLAN_RETRY_LIMIT_OPTIONS,
            "transfer_retry_seconds": TRANSFER_RETRY_SECOND_OPTIONS,
            "transfer_retry_limit": TRANSFER_RETRY_LIMIT_OPTIONS,
            "migration_settle_seconds": MIGRATION_SETTLE_OPTIONS,
            "post_transfer_wait_seconds": POST_TRANSFER_WAIT_OPTIONS,
            "status_refresh_seconds": STATUS_REFRESH_OPTIONS,
        }
        if key not in allowed or value not in allowed[key]:
            return False
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            cursor = db.execute(
                "UPDATE bot_settings SET setting_value = ? WHERE setting_key = ?",
                (value, key),
            )
            return cursor.rowcount == 1

    def _authorized_user_ids(self) -> list[int]:
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            rows = db.execute("SELECT user_id FROM authorized_users ORDER BY user_id").fetchall()
        return [int(row[0]) for row in rows]

    def _add_authorized_user(self, user_id: int) -> bool:
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO authorized_users(user_id) VALUES (?)", (user_id,)
            )
            db.execute("INSERT OR IGNORE INTO user_quotas(user_id) VALUES (?)", (user_id,))
            return cursor.rowcount == 1

    def _remove_authorized_user(self, user_id: int) -> bool:
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            cursor = db.execute("DELETE FROM authorized_users WHERE user_id = ?", (user_id,))
            return cursor.rowcount == 1

    def _quota_info(self, user_id: int):
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            row = db.execute(
                "SELECT granted_total, remaining, attempts, successes, failures, stopped "
                "FROM user_quotas WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return None
        keys = ("granted", "remaining", "attempts", "successes", "failures", "stopped")
        return dict(zip(keys, map(int, row)))

    def _grant_activations(self, user_id: int, amount: int) -> bool:
        if amount <= 0:
            return False
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            cursor = db.execute(
                "UPDATE user_quotas SET granted_total = granted_total + ?, remaining = remaining + ? "
                "WHERE user_id = ? AND EXISTS (SELECT 1 FROM authorized_users WHERE user_id = ?)",
                (amount, amount, user_id, user_id),
            )
            return cursor.rowcount == 1

    def _reserve_activation(self, user_id: int, target: int):
        """Consume one activation atomically when an authorized user's run is confirmed."""
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            cursor = db.execute(
                "UPDATE user_quotas SET remaining = remaining - 1, attempts = attempts + 1 "
                "WHERE user_id = ? AND remaining > 0 "
                "AND EXISTS (SELECT 1 FROM authorized_users WHERE user_id = ?)",
                (user_id, user_id),
            )
            if cursor.rowcount != 1:
                return None
            inserted = db.execute(
                "INSERT INTO activations(user_id, target, status) VALUES (?, ?, 'running')",
                (user_id, target),
            )
            return int(inserted.lastrowid)

    def _refund_activation(self, activation_id: int):
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            row = db.execute(
                "SELECT user_id FROM activations WHERE id = ? AND status = 'running'",
                (activation_id,),
            ).fetchone()
            if row is None:
                return
            db.execute(
                "UPDATE user_quotas SET remaining = remaining + 1, "
                "attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END WHERE user_id = ?",
                (row[0],),
            )
            db.execute("DELETE FROM activations WHERE id = ?", (activation_id,))

    def _activation_completed(self, user_id: int, activation_id: int, outcome: str):
        status = outcome if outcome in {"success", "failed", "stopped"} else "failed"
        counter = {"success": "successes", "failed": "failures", "stopped": "stopped"}[status]
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            cursor = db.execute(
                "UPDATE activations SET status = ?, finished_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND user_id = ? AND status = 'running'",
                (status, activation_id, user_id),
            )
            if cursor.rowcount == 1:
                db.execute(
                    f"UPDATE user_quotas SET {counter} = {counter} + 1 WHERE user_id = ?",
                    (user_id,),
                )

    def _activation_history(self, user_id: int, limit=10):
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            rows = db.execute(
                "SELECT target, status, started_at, finished_at FROM activations "
                "WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
            ).fetchall()
        return rows

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> tuple[bool, str | None]:
        with self._lock:
            return self.is_running(), self._error

    def start(self) -> bool:
        with self._lock:
            if self.is_running():
                return False
            if not self._token or not self._admin_id or self._runner is None:
                self._error = "Telegram bot configuration is incomplete."
                return False
            self._error = None
            self._thread = threading.Thread(
                target=self._thread_main,
                name="telegram-polling-bot",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self):
        with self._lock:
            loop, stop_event = self._loop, self._stop_event
        if loop is not None and stop_event is not None and loop.is_running():
            loop.call_soon_threadsafe(stop_event.set)

    def _thread_main(self):
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            with self._lock:
                self._error = str(exc)

    async def _serve(self):
        with self._lock:
            token = self._token
            admin_id = self._admin_id
            self._loop = asyncio.get_running_loop()
            self._stop_event = asyncio.Event()
            stop_event = self._stop_event
            runner = self._runner

        if not token:
            raise RuntimeError("Telegram bot token is missing.")

        application = Application.builder().token(token).build()
        conversation = ConversationHandler(
            entry_points=[
                CommandHandler("run", self._run_command),
                MessageHandler(filters.Regex(r"^▶️ تشغيل$"), self._run_button),
            ],
            states={
                TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._target_message)],
                CONSENT: [CallbackQueryHandler(self._consent_callback, pattern=r"^credentials:(continue|cancel)$")],
                OWNER_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._owner_email_message)],
                OWNER_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._owner_password_message)],
                CHILD_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._child_email_message)],
                CHILD_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._child_password_message)],
                CONFIRM_RUN: [CallbackQueryHandler(self._confirm_run, pattern=r"^run:(confirm|cancel)$")],
                ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, self._conversation_timeout)],
            },
            fallbacks=[
                CommandHandler("cancel", self._cancel_command),
                MessageHandler(filters.Regex(r"^❌ إلغاء$"), self._cancel_command),
            ],
            per_chat=True,
            per_user=True,
            allow_reentry=True,
            conversation_timeout=600,
        )
        application.add_handler(CommandHandler("admin", self._admin_panel))
        application.add_handler(CommandHandler("adduser", self._add_user_command))
        application.add_handler(CommandHandler("removeuser", self._remove_user_command))
        application.add_handler(CommandHandler("grant", self._grant_command))
        application.add_handler(CommandHandler("users", self._users_command))
        application.add_handler(CommandHandler("userstats", self._userstats_command))
        application.add_handler(CommandHandler("settings", self._settings_command))
        application.add_handler(MessageHandler(filters.Regex(r"^👥 لوحة الإدارة$"), self._admin_panel))
        application.add_handler(CallbackQueryHandler(self._admin_callback, pattern=r"^admin:(users|add_help|remove_help|grant_help|settings)$"))
        application.add_handler(CallbackQueryHandler(self._settings_callback, pattern=r"^settings:(plan_retry_minutes|plan_retry_limit|transfer_retry_seconds|transfer_retry_limit|migration_settle_seconds|post_transfer_wait_seconds|status_refresh_seconds|noop):\d+$"))
        application.add_handler(CommandHandler("start", self._start_command))
        application.add_handler(CommandHandler("help", self._help_command))
        application.add_handler(MessageHandler(filters.Regex(r"^ℹ️ مساعدة$"), self._help_command))
        application.add_handler(MessageHandler(filters.Regex(r"^🏠 البداية$"), self._start_command))
        application.add_handler(CommandHandler("status", self._status_command))
        application.add_handler(MessageHandler(filters.Regex(r"^📊 الحالة$"), self._status_command))
        application.add_handler(CommandHandler("stop", self._stop_command))
        application.add_handler(MessageHandler(filters.Regex(r"^⏹ إيقاف$"), self._stop_command))
        application.add_handler(conversation)
        application.add_handler(CommandHandler("cancel", self._cancel_command))
        application.add_handler(MessageHandler(filters.Regex(r"^❌ إلغاء$"), self._cancel_command))

        initialized = False
        started = False
        polling = False
        try:
            await application.initialize()
            initialized = True
            await application.start()
            started = True
            if application.updater is None:
                raise RuntimeError("Telegram polling updater is unavailable.")
            await application.updater.start_polling(drop_pending_updates=True)
            polling = True
            runner.add_log("Telegram polling بدأ بنجاح.")
            try:
                await application.bot.send_message(
                    chat_id=admin_id,
                    text="✅ بوت اتصالات شغّال الآن ويستقبل الأوامر.\nاستخدم زر لوحة الإدارة أو أرسل /admin لإدارة المستخدمين.",
                    reply_markup=self._main_keyboard(is_admin=True),
                )
            except Exception:
                runner.add_log(
                    "تعذر إرسال رسالة بدء التشغيل للمدير؛ افتح محادثة البوت وأرسل /start أولاً."
                )
            await stop_event.wait()
        finally:
            if polling and application.updater is not None:
                await application.updater.stop()
            if started:
                await application.stop()
            if initialized:
                await application.shutdown()
            with self._lock:
                self._loop = None
                self._stop_event = None

    def _is_private(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        return user is not None and chat is not None and chat.type == "private"

    def _is_admin(self, update: Update) -> bool:
        if not self._is_private(update):
            return False
        with self._lock:
            admin_id = self._admin_id
        return admin_id is not None and update.effective_user.id == admin_id

    def _is_admin_id(self, user_id: int) -> bool:
        with self._lock:
            return self._admin_id == user_id

    def _is_authorized(self, update: Update) -> bool:
        if not self._is_private(update):
            return False
        user_id = update.effective_user.id
        if self._is_admin(update):
            return True
        return user_id in self._authorized_user_ids()

    async def _deny(self, update: Update):
        message = update.effective_message
        if message is not None:
            user = update.effective_user
            suffix = f" رقم Telegram ID الخاص بك: {user.id}. أرسله للمسؤول لإضافتك." if user else ""
            await message.reply_text("⛔ غير مصرح لك باستخدام البوت." + suffix)

    async def _delete_sensitive_message(self, message):
        """Try to remove a credential message from the private chat immediately."""
        try:
            await message.delete()
        except Exception:
            # Never log message text or credentials, even if Telegram deletion fails.
            pass

    def _draft(self, context: ContextTypes.DEFAULT_TYPE) -> dict:
        draft = context.user_data.get("run_draft")
        if draft is None:
            draft = {}
            context.user_data["run_draft"] = draft
        return draft

    @staticmethod
    def _main_keyboard(is_admin=False):
        rows = [
            [KeyboardButton(BUTTON_RUN), KeyboardButton(BUTTON_STATUS)],
            [KeyboardButton(BUTTON_STOP), KeyboardButton(BUTTON_CANCEL)],
            [KeyboardButton(BUTTON_HELP), KeyboardButton(BUTTON_HOME)],
        ]
        if is_admin:
            rows.append([KeyboardButton(BUTTON_ADMIN)])
        return ReplyKeyboardMarkup(
            rows,
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="اختار أمر من الأزرار",
        )

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return
        is_admin = self._is_admin(update)
        text = "✅ البوت شغال ويستقبل الأوامر.\nأهلًا! اختار زر من القائمة: تشغيل، الحالة، إيقاف، أو إلغاء.\nتقدر تستخدم /run 50000 أو /help."
        if is_admin:
            text += "\nلوحة الإدارة والإعدادات: /admin و/settings."
        else:
            stats = self._quota_info(update.effective_user.id) or {"remaining": 0}
            text += f"\nالتفعيلات المتبقية: {stats['remaining']}."
        await update.effective_message.reply_text(
            text,
            reply_markup=self._main_keyboard(is_admin=is_admin),
        )

    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return
        text = (
            "▶️ تشغيل: اكتب الهدف ثم راجع ملخص النظام قبل التأكيد.\n"
            "📊 الحالة: تعرض حالة التشغيل.\n"
            "⏹ إيقاف: يوقف التشغيل الحالي بعد انتهاء طلب الشبكة.\n"
            "❌ إلغاء: يلغي إدخال بيانات التشغيل.\n"
            "الأوامر: /run 50000، /status، /stop، /cancel."
        )
        if self._is_admin(update):
            text += "\n👥 إدارة المستخدمين والإعدادات: /admin و/settings."
        await update.effective_message.reply_text(
            text,
            reply_markup=self._main_keyboard(is_admin=self._is_admin(update)),
        )

    @staticmethod
    def _admin_keyboard():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 عرض المستخدمين", callback_data="admin:users")],
            [
                InlineKeyboardButton("➕ طريقة الإضافة", callback_data="admin:add_help"),
                InlineKeyboardButton("➖ طريقة الحذف", callback_data="admin:remove_help"),
            ],
            [InlineKeyboardButton("🎟 منح تفعيلات", callback_data="admin:grant_help")],
            [InlineKeyboardButton("⚙️ إعدادات التغيير والحالة", callback_data="admin:settings")],
        ])

    @staticmethod
    def _settings_keyboard(settings):
        def button(label, key, value, selected):
            return InlineKeyboardButton(
                ("✅ " if selected else "") + label,
                callback_data=f"settings:{key}:{value}",
            )

        retry_rows = [
            [button(f"{minutes} د", "plan_retry_minutes", minutes, settings["plan_retry_minutes"] == minutes)
             for minutes in (1, 2, 3, 4)],
            [button(f"{minutes} د", "plan_retry_minutes", minutes, settings["plan_retry_minutes"] == minutes)
             for minutes in (5, 10, 15)],
        ]
        limit_rows = [[
            button("1", "plan_retry_limit", 1, settings["plan_retry_limit"] == 1),
            button("3", "plan_retry_limit", 3, settings["plan_retry_limit"] == 3),
            button("5", "plan_retry_limit", 5, settings["plan_retry_limit"] == 5),
            button("10", "plan_retry_limit", 10, settings["plan_retry_limit"] == 10),
            button("بلا حد", "plan_retry_limit", 0, settings["plan_retry_limit"] == 0),
        ]]
        transfer_interval_rows = [
            [button(f"{seconds} ث", "transfer_retry_seconds", seconds,
                    settings["transfer_retry_seconds"] == seconds)
             for seconds in TRANSFER_RETRY_SECOND_OPTIONS[:3]],
            [button(f"{seconds} ث", "transfer_retry_seconds", seconds,
                    settings["transfer_retry_seconds"] == seconds)
             for seconds in TRANSFER_RETRY_SECOND_OPTIONS[3:]],
        ]
        transfer_limit_rows = [[
            button("1", "transfer_retry_limit", 1, settings["transfer_retry_limit"] == 1),
            button("3", "transfer_retry_limit", 3, settings["transfer_retry_limit"] == 3),
            button("5", "transfer_retry_limit", 5, settings["transfer_retry_limit"] == 5),
            button("10", "transfer_retry_limit", 10, settings["transfer_retry_limit"] == 10),
            button("بلا حد", "transfer_retry_limit", 0, settings["transfer_retry_limit"] == 0),
        ]]
        settle_rows = [[
            button(f"{seconds} ث", "migration_settle_seconds", seconds,
                    settings["migration_settle_seconds"] == seconds)
            for seconds in MIGRATION_SETTLE_OPTIONS
        ]]
        post_transfer_rows = [[
            button(f"{seconds} ث", "post_transfer_wait_seconds", seconds,
                    settings["post_transfer_wait_seconds"] == seconds)
            for seconds in POST_TRANSFER_WAIT_OPTIONS
        ]]
        refresh_rows = [[
            button(f"{seconds} ث", "status_refresh_seconds", seconds, settings["status_refresh_seconds"] == seconds)
            for seconds in STATUS_REFRESH_OPTIONS
        ]]
        return InlineKeyboardMarkup(
            retry_rows
            + [[InlineKeyboardButton("— حد إعادات تغيير النظام —", callback_data="settings:noop:0")]]
            + limit_rows
            + [[InlineKeyboardButton("— مهلة إعادة محاولة التحويل —", callback_data="settings:noop:0")]]
            + transfer_interval_rows
            + [[InlineKeyboardButton("— حد إعادات التحويل —", callback_data="settings:noop:0")]]
            + transfer_limit_rows
            + [[InlineKeyboardButton("— انتظار استقرار النظام بعد التغيير —", callback_data="settings:noop:0")]]
            + settle_rows
            + [[InlineKeyboardButton("— مهلة بعد نجاح التحويل —", callback_data="settings:noop:0")]]
            + post_transfer_rows
            + [[InlineKeyboardButton("— تحديث رسالة الحالة —", callback_data="settings:noop:0")]]
            + refresh_rows
        )

    @staticmethod
    def _settings_text(settings):
        plan_limit = "بلا حد" if settings["plan_retry_limit"] == 0 else str(settings["plan_retry_limit"])
        transfer_limit = "بلا حد" if settings["transfer_retry_limit"] == 0 else str(settings["transfer_retry_limit"])
        return (
            "⚙️ إعدادات التشغيل التلقائي\n\n"
            f"⏱ مهلة إعادة طلب تغيير النظام: كل {settings['plan_retry_minutes']} دقائق.\n"
            f"🔁 أقصى إعادات تغيير النظام بعد الطلب الأول: {plan_limit}.\n"
            f"🔄 فاصل إعادة التحويل ثابت: {settings['transfer_retry_seconds']} ث؛ "
            f"وعدد الإعادات {transfer_limit} بعد المحاولة الأولى.\n"
            f"⏳ انتظار الاستقرار بعد تغيير النظام: {settings['migration_settle_seconds']} ثانية.\n"
            f"⏱ مهلة بعد نجاح التحويل: {settings['post_transfer_wait_seconds']} ثانية.\n"
            f"📲 تحديث رسالة الحالة: كل {settings['status_refresh_seconds']} ثوانٍ.\n\n"
            "تُطبّق الإعدادات أثناء التشغيل. لا يعيد البوت طلب تغيير النظام بعد مهلة الإعداد إلا "
            "عند قراءة ناجحة تؤكد أن النظام القديم ما زال قائمًا؛ القراءة المتعذرة تعني الانتظار. "
            "التحويل يُراجع باستعلام الكمية القابلة للتحويل، وأي نتيجة ملتبسة لا تُكرر حتى يؤكد الاستعلام "
            "أن الكمية نفسها ما زالت متاحة. عند بلوغ حد إعادة التحويل يستمر التحقق دون إرسال طلبات إضافية."
        )

    async def _settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        settings = self._bot_settings()
        await update.effective_message.reply_text(
            self._settings_text(settings), reply_markup=self._settings_keyboard(settings)
        )

    async def _settings_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not self._is_admin(update):
            await query.answer("الإعدادات للمسؤول الرئيسي فقط.", show_alert=True)
            return
        parts = (query.data or "").split(":")
        if len(parts) != 3:
            await query.answer("إعداد غير صالح.", show_alert=True)
            return
        key, raw_value = parts[1], parts[2]
        try:
            value = int(raw_value)
        except ValueError:
            await query.answer("قيمة غير صالحة.", show_alert=True)
            return
        if key == "noop":
            await query.answer()
            return
        if not self._set_bot_setting(key, value):
            await query.answer("القيمة غير مدعومة.", show_alert=True)
            return
        await query.answer("تم تحديث الإعداد وسيُطبّق أثناء التشغيل.")
        settings = self._bot_settings()
        await query.edit_message_text(
            self._settings_text(settings), reply_markup=self._settings_keyboard(settings)
        )

    async def _admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        await update.effective_message.reply_text(
            "👥 لوحة إدارة المستخدمين\n"
            "إضافة: /adduser ثم رقم Telegram ID.\n"
            "منح تفعيلات: /grant ثم ID ثم العدد.\n"
            "عرض القائمة والتقارير: /users أو /userstats ثم ID.\n"
            "إعداد مهلة إعادة تغيير النظام وحدّ الإعادات وتحديث الحالة: /settings.\n"
            "حذف: /removeuser ثم رقم Telegram ID.\n\n"
            "التفعيل يُخصم عند بدء التشغيل المؤكد؛ وتُسجّل المحاولات والنجاحات والإخفاقات والإيقافات.\n"
            "كل مستخدم يشغّل ببياناته بنفسه، وتشغيل واحد فقط مسموح في نفس الوقت.",
            reply_markup=self._admin_keyboard(),
        )

    async def _admin_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not self._is_admin(update):
            await query.answer("هذا القسم للمسؤول فقط.", show_alert=True)
            return
        await query.answer()
        if query.data == "admin:users":
            ids = self._authorized_user_ids()
            with self._lock:
                admin_id = self._admin_id
            lines = [f"• {admin_id} — المسؤول الرئيسي (غير محدود)"]
            for user_id in ids:
                if user_id == admin_id:
                    continue
                stats = self._quota_info(user_id) or {}
                lines.append(
                    f"• {user_id}: ممنوح {stats.get('granted', 0)}، متبقي {stats.get('remaining', 0)}، "
                    f"محاولات {stats.get('attempts', 0)}، نجاح {stats.get('successes', 0)}"
                )
            await query.edit_message_text(
                "المستخدمون والتفعيلات:\n" + "\n".join(lines)[-3800:],
                reply_markup=self._admin_keyboard(),
            )
        elif query.data == "admin:add_help":
            await query.edit_message_text(
                "لإضافة شخص: خليه يفتح البوت ويرسل /start، ثم يبعث لك رقم Telegram ID الظاهر له.\n"
                "بعدها أرسل: /adduser 123456789",
                reply_markup=self._admin_keyboard(),
            )
        elif query.data == "admin:grant_help":
            await query.edit_message_text(
                "لمنح تفعيلات لمستخدم مضاف أرسل: /grant 123456789 10.\n"
                "يُخصم تفعيل واحد عند تأكيد بدء التشغيل، وتُسجل النتيجة عند انتهاء المحاولة.",
                reply_markup=self._admin_keyboard(),
            )
        elif query.data == "admin:settings":
            settings = self._bot_settings()
            await query.edit_message_text(
                self._settings_text(settings), reply_markup=self._settings_keyboard(settings)
            )
        else:
            await query.edit_message_text(
                "لحذف مستخدم أرسل: /removeuser 123456789.\n"
                "لن يمكن حذف المسؤول الرئيسي المحدد في ADMIN_ID.",
                reply_markup=self._admin_keyboard(),
            )

    @staticmethod
    def _parse_user_id(args):
        if len(args) != 1 or not args[0].isdigit():
            return None
        user_id = int(args[0])
        return user_id if 0 < user_id <= 9_223_372_036_854_775_807 else None

    async def _add_user_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        user_id = self._parse_user_id(context.args)
        if user_id is None:
            await update.effective_message.reply_text("الاستخدام: /adduser 123456789 (رقم Telegram ID فقط).")
            return
        if self._is_admin_id(user_id):
            await update.effective_message.reply_text("أنت المسؤول الرئيسي بالفعل.")
            return
        if self._add_authorized_user(user_id):
            await update.effective_message.reply_text(
                f"✅ تمت إضافة المستخدم {user_id} بصفر تفعيلات. اطلب منه /start ثم امنحه الرصيد بـ /grant {user_id} 10."
            )
        else:
            await update.effective_message.reply_text("هذا المستخدم موجود بالفعل في القائمة.")

    async def _remove_user_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        user_id = self._parse_user_id(context.args)
        if user_id is None:
            await update.effective_message.reply_text("الاستخدام: /removeuser 123456789 (رقم Telegram ID فقط).")
            return
        if self._is_admin_id(user_id):
            await update.effective_message.reply_text("لا يمكن حذف المسؤول الرئيسي.")
            return
        if not self._remove_authorized_user(user_id):
            await update.effective_message.reply_text("هذا الرقم غير موجود في قائمة المستخدمين.")
            return
        if self._active_user_id == user_id and self._runner.is_running():
            self._runner.stop()
            self._active_user_id = None
        await update.effective_message.reply_text(f"✅ تم حذف المستخدم {user_id} من القائمة.")

    async def _grant_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        if len(context.args) != 2 or not context.args[1].isdigit():
            await update.effective_message.reply_text("الاستخدام: /grant 123456789 10")
            return
        user_id = self._parse_user_id(context.args[:1])
        amount = int(context.args[1])
        if user_id is None or not 1 <= amount <= 1_000_000:
            await update.effective_message.reply_text("اكتب Telegram ID صحيحًا وعدد تفعيلات من 1 إلى 1,000,000.")
            return
        if not self._grant_activations(user_id, amount):
            await update.effective_message.reply_text("أضف المستخدم أولًا باستخدام /adduser ID.")
            return
        stats = self._quota_info(user_id)
        await update.effective_message.reply_text(
            f"✅ تمت إضافة {amount} تفعيل للمستخدم {user_id}. المتاح الآن: {stats['remaining']}."
        )

    async def _users_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        with self._lock:
            admin_id = self._admin_id
        lines = [f"• {admin_id} — المسؤول الرئيسي (غير محدود)"]
        for user_id in self._authorized_user_ids():
            if user_id == admin_id:
                continue
            stats = self._quota_info(user_id) or {}
            lines.append(
                f"• {user_id}: ممنوح {stats.get('granted', 0)} | متبقي {stats.get('remaining', 0)} | "
                f"محاولات {stats.get('attempts', 0)} | نجاح {stats.get('successes', 0)} | "
                f"فشل {stats.get('failures', 0)} | إيقاف {stats.get('stopped', 0)}"
            )
        await update.effective_message.reply_text("المستخدمون وتفعيلاتهم:\n" + "\n".join(lines)[-3800:])

    async def _userstats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        user_id = self._parse_user_id(context.args)
        if user_id is None:
            await update.effective_message.reply_text("الاستخدام: /userstats 123456789")
            return
        stats = self._quota_info(user_id)
        if stats is None:
            await update.effective_message.reply_text("لا توجد سجلات لهذا المستخدم حتى الآن.")
            return
        history = self._activation_history(user_id)
        lines = [
            f"تقرير المستخدم {user_id}",
            f"ممنوح تراكميًا: {stats['granted']}",
            f"المتبقي: {stats['remaining']}",
            f"المحاولات التي بدأت: {stats['attempts']}",
            f"ناجح: {stats['successes']} | فاشل: {stats['failures']} | متوقف: {stats['stopped']}",
            "آخر المحاولات:",
        ]
        lines.extend(
            f"• الهدف {target:,} — {status} — بدأ {started_at}"
            + (f" — انتهى {finished_at}" if finished_at else "")
            for target, status, started_at, finished_at in history
        )
        await update.effective_message.reply_text("\n".join(lines)[-3800:])

    def _has_activation(self, update: Update) -> bool:
        if self._is_admin(update):
            return True
        stats = self._quota_info(update.effective_user.id)
        return stats is not None and stats["remaining"] > 0

    async def _run_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return ConversationHandler.END
        if self._runner.is_running():
            await update.effective_message.reply_text("فيه تشغيل قائم بالفعل. اختار الحالة أو أوقفه قبل تشغيل جديد.")
            return ConversationHandler.END
        if not self._has_activation(update):
            await update.effective_message.reply_text("رصيد تفعيلاتك انتهى؛ تواصل مع المسؤول لمنحك تفعيلات جديدة.")
            return ConversationHandler.END
        context.user_data.pop("run_draft", None)
        await update.effective_message.reply_text(
            f"اكتب هدف الميكسات (من 1 إلى {MAX_TARGET_MIX:,})، أو اضغط إلغاء.",
            reply_markup=self._main_keyboard(is_admin=self._is_admin(update)),
        )
        return TARGET

    async def _run_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return ConversationHandler.END
        if self._runner.is_running():
            await update.effective_message.reply_text("فيه تشغيل قائم بالفعل. اختار الحالة أو أوقفه قبل تشغيل جديد.")
            return ConversationHandler.END
        if not self._has_activation(update):
            await update.effective_message.reply_text("رصيد تفعيلاتك انتهى؛ تواصل مع المسؤول لمنحك تفعيلات جديدة.")
            return ConversationHandler.END
        if not context.args:
            context.user_data.pop("run_draft", None)
            await update.effective_message.reply_text("اكتب هدف الميكسات، مثال: 500000.")
            return TARGET
        try:
            target = int(context.args[0].replace(",", ""))
        except ValueError:
            await update.effective_message.reply_text("الهدف لازم يكون رقمًا صحيحًا من الميكسات.")
            return ConversationHandler.END
        return await self._request_run_consent(update, context, target)

    async def _target_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return ConversationHandler.END
        try:
            target = int((update.effective_message.text or "").replace(",", "").strip())
        except ValueError:
            await update.effective_message.reply_text("اكتب هدفًا رقميًا واضحًا، أو اضغط إلغاء.")
            return TARGET
        return await self._request_run_consent(update, context, target)

    async def _request_run_consent(self, update: Update, context: ContextTypes.DEFAULT_TYPE, target: int):
        if not 1 <= target <= MAX_TARGET_MIX:
            await update.effective_message.reply_text(f"الهدف لازم يكون بين 1 و{MAX_TARGET_MIX:,} ميكس.")
            return ConversationHandler.END
        context.user_data.pop("run_draft", None)
        self._draft(context)["target"] = target
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("أوافق وأكمل", callback_data="credentials:continue"),
                InlineKeyboardButton("إلغاء", callback_data="credentials:cancel"),
            ]]
        )
        await update.effective_message.reply_text(
            f"الهدف: {target:,} ميكس.\n"
            "بعد إدخال بيانات الحسابين، سأقرأ النظام الحالي قبل بدء العمليات وأعرضه في الحالة.\n\n"
            "⚠️ ستدخل بيانات الحسابين في Telegram؛ سأحاول حذف رسائلها بعد الاستلام. أكمل فقط إذا كنت موافقًا.",
            reply_markup=keyboard,
        )
        return CONSENT

    async def _consent_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not self._is_authorized(update):
            await query.answer("غير مصرح", show_alert=True)
            return ConversationHandler.END
        await query.answer()
        if query.data.endswith("cancel"):
            context.user_data.pop("run_draft", None)
            await query.edit_message_text("تم الإلغاء.")
            return ConversationHandler.END
        await query.edit_message_text("أرسل إيميل حساب الأونر في هذه المحادثة الخاصة.")
        return OWNER_EMAIL

    async def _owner_email_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return ConversationHandler.END
        message = update.effective_message
        email = (message.text or "").strip()
        if "@" not in email:
            await self._delete_sensitive_message(message)
            await message.reply_text("الإيميل غير واضح. أرسله مرة أخرى، أو استخدم /cancel.")
            return OWNER_EMAIL
        self._draft(context)["owner_email"] = email
        await self._delete_sensitive_message(message)
        await message.reply_text("أرسل باسورد حساب الأونر. سأحاول حذف رسالة الباسورد فور استلامها.")
        return OWNER_PASSWORD

    async def _owner_password_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return ConversationHandler.END
        message = update.effective_message
        password = (message.text or "").strip()
        await self._delete_sensitive_message(message)
        if not password:
            await message.reply_text("الباسورد فارغ. أرسله مرة أخرى، أو استخدم /cancel.")
            return OWNER_PASSWORD
        self._draft(context)["owner_password"] = password
        await message.reply_text("أرسل إيميل حساب الفرد.")
        return CHILD_EMAIL

    async def _child_email_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return ConversationHandler.END
        message = update.effective_message
        email = (message.text or "").strip()
        if "@" not in email:
            await self._delete_sensitive_message(message)
            await message.reply_text("الإيميل غير واضح. أرسله مرة أخرى، أو استخدم /cancel.")
            return CHILD_EMAIL
        self._draft(context)["child_email"] = email
        await self._delete_sensitive_message(message)
        await message.reply_text("أرسل باسورد حساب الفرد. سأحاول حذف رسالة الباسورد فور استلامها.")
        return CHILD_PASSWORD

    async def _child_password_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return ConversationHandler.END
        message = update.effective_message
        password = (message.text or "").strip()
        await self._delete_sensitive_message(message)
        if not password:
            await message.reply_text("الباسورد فارغ. أرسله مرة أخرى، أو استخدم /cancel.")
            return CHILD_PASSWORD
        self._draft(context)["child_password"] = password
        target = self._draft(context).get("target", 0)
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ تأكيد وبدء التشغيل", callback_data="run:confirm"),
                InlineKeyboardButton("إلغاء", callback_data="run:cancel"),
            ]]
        )
        await message.reply_text(
            f"البيانات استُلمت مؤقتًا في الذاكرة. بدء التشغيل سيجدد/يغيّر باقات ويحوّل ميكسات.\n"
            f"الهدف: {target:,} ميكس. هل تؤكد بدء العمليات؟",
            reply_markup=keyboard,
        )
        return CONFIRM_RUN

    async def _confirm_run(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not self._is_authorized(update):
            await query.answer("غير مصرح", show_alert=True)
            return ConversationHandler.END
        await query.answer()
        if query.data.endswith("cancel"):
            context.user_data.pop("run_draft", None)
            await query.edit_message_text("تم الإلغاء. لم يبدأ أي تشغيل.")
            return ConversationHandler.END

        draft = context.user_data.pop("run_draft", {})
        user_id = update.effective_user.id
        target = None
        activation_id = None
        try:
            target = int(draft["target"])
            if not self._is_admin(update):
                activation_id = self._reserve_activation(user_id, target)
                if activation_id is None:
                    await query.edit_message_text("رصيد تفعيلاتك انتهى أو لم يعد متاحًا؛ تواصل مع المسؤول.")
                    return ConversationHandler.END
            started = self._runner.start(
                draft["owner_email"],
                draft["owner_password"],
                draft["child_email"],
                draft["child_password"],
                target,
                request_user_id=user_id,
                activation_id=activation_id,
            )
        except (KeyError, TypeError, ValueError):
            if activation_id is not None:
                self._refund_activation(activation_id)
            await query.edit_message_text("انتهت بيانات الطلب أو لم تكتمل. ابدأ من جديد باستخدام /run.")
            return ConversationHandler.END
        except Exception:
            if activation_id is not None:
                self._refund_activation(activation_id)
            await query.edit_message_text("تعذر بدء التشغيل؛ لم يُخصم التفعيل. حاول لاحقًا أو أبلغ المسؤول.")
            return ConversationHandler.END
        if started:
            with self._lock:
                self._active_user_id = user_id
            remaining = self._quota_info(user_id)
            suffix = (
                f" المتبقي: {remaining['remaining']} تفعيل."
                if activation_id is not None and remaining is not None
                else ""
            )
            live_message = await query.edit_message_text(
                "✅ بدأ التشغيل." + suffix + "\nستتحدث رسالة الحالة تلقائيًا. استخدم /stop للإيقاف."
            )
            status_message = live_message if getattr(live_message, "message_id", None) else query.message
            self._start_live_status_updates(context, status_message, user_id)
        else:
            if activation_id is not None:
                self._refund_activation(activation_id)
            await query.edit_message_text("يوجد تشغيل قائم بالفعل؛ لم أبدأ تشغيلًا آخر ولم أستهلك التفعيل.")
        return ConversationHandler.END

    async def _cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return ConversationHandler.END
        context.user_data.pop("run_draft", None)
        await update.effective_message.reply_text("أُلغي الطلب ومُسحت بياناته المؤقتة من ذاكرة المحادثة.")
        return ConversationHandler.END

    async def _conversation_timeout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.pop("run_draft", None)
        message = update.effective_message
        if message is not None:
            await message.reply_text("انتهت مهلة إدخال البيانات؛ مُسحت البيانات المؤقتة. ابدأ من جديد بـ /run.")
        return ConversationHandler.END

    @staticmethod
    def _plan_label(plan_id):
        return {
            "Hekaya_XRP_MIX_Parent_7": "حكاية ميكسات 270",
            "Hekaya_XRP_MIX_Parent_6": "حكاية ميكسات 180",
            "Hekaya_XRP_MIX_Parent_5": "حكاية ميكسات 240",
            "Hekaya_XRP_MIX_Parent_8": "حكاية ميكسات 52",
        }.get(plan_id, plan_id or "غير متاح")

    def _live_status_text(self):
        logs, error = self._runner.snapshot()
        state = self._runner.live_state() if hasattr(self._runner, "live_state") else {}
        running = self._runner.is_running()
        if running:
            text = "🟢 التشغيل يعمل"
        else:
            text = "✅ اكتمل التشغيل" if state.get("outcome") == "success" else "⚪ انتهى التشغيل"
        if state.get("status"):
            text += f"\n📍 المرحلة: {state['status']}"
        current = state.get("current_plan_id")
        target = state.get("target_plan_id")
        if current or target:
            text += f"\n📦 النظام الحالي: {self._plan_label(current)}"
        if state.get("current_mix") is not None:
            text += f"\n📊 الرصيد: {state['current_mix']:,} / {state.get('target_mix', 0):,} ميكس"
        if state.get("cycle_count") is not None:
            text += f"\n🔄 الدورة: {state['cycle_count']}"
        if state.get("transfer_retry_limit") is not None:
            transfer_retries = int(state.get("transfer_retry_limit", 3))
            transfer_attempts = int(state.get("transfer_attempts", 0))
            transfer_limit_label = (
                "بلا حد" if transfer_retries == 0 else str(transfer_retries + 1)
            )
            text += f"\n🔁 محاولات التحويل: {transfer_attempts}/{transfer_limit_label}"
            if state.get("transfer_amount") is not None:
                text += f"\n📨 الكمية المستعلم عنها: {int(state['transfer_amount']):,} ميكس"
            if state.get("transfer_retry_wait_seconds") is not None:
                text += f"\n⏱ الانتظار قبل الفحص/المحاولة التالية: {state['transfer_retry_wait_seconds']} ث"
            if state.get("transfer_next_retry_seconds") is not None:
                text += f"\n⏱ فاصل المحاولة التالية الثابت: {state['transfer_next_retry_seconds']} ث"
        if target:
            text += f"\n🎯 النظام المطلوب: {self._plan_label(target)}"
            interval = max(1, int(state.get("retry_interval_seconds", 240)))
            started = state.get("request_started")
            retries = int(state.get("retries_done", 0))
            limit = int(state.get("retry_limit", 3))
            limit_label = "بلا حد" if limit == 0 else str(limit)
            text += f"\n🔁 إعادات طلب النظام: {retries}/{limit_label}"
            if limit and retries >= limit:
                text += "\n⏳ تم بلوغ حد الإعادات؛ سأواصل المراقبة دون طلبات أخرى."
            elif started is not None:
                remaining = max(0, int(interval - (time.monotonic() - started)))
                text += f"\n⏱ مهلة إعادة الطلب: {remaining // 60:02d}:{remaining % 60:02d}"
                text += "\nℹ️ الإعادة لا تتم إلا بعد قراءة ناجحة تؤكد بقاء النظام القديم."
                if state.get("last_status_read") is None:
                    text += "\nℹ️ بانتظار أول قراءة حالة مؤكدة."
        text += f"\n🕒 آخر تحديث: {datetime.now().strftime('%H:%M:%S')}"
        if error:
            text += f"\n⚠️ آخر خطأ: {error}"
        if logs:
            text += "\n\nآخر التحديثات:\n" + "\n".join(logs[-4:])
        return text[-3800:]

    def _start_live_status_updates(self, context, message, user_id):
        job_queue = getattr(context.application, "job_queue", None)
        if job_queue is None or message is None:
            return
        chat_id = getattr(message, "chat_id", None)
        if chat_id is None and getattr(message, "chat", None) is not None:
            chat_id = message.chat.id
        message_id = getattr(message, "message_id", None)
        if chat_id is None or message_id is None:
            return
        with self._lock:
            if self._live_status_job is not None:
                self._live_status_job.schedule_removal()
            self._live_status_target = {
                "chat_id": int(chat_id),
                "message_id": int(message_id),
                "user_id": int(user_id),
            }
            self._live_status_job = job_queue.run_repeating(
                self._update_live_status,
                interval=1.0,
                first=0.0,
                data={
                    "chat_id": int(chat_id),
                    "message_id": int(message_id),
                    "user_id": int(user_id),
                    "last_edit_at": 0.0,
                    "last_text": None,
                    "final_pending": False,
                },
                name=f"live-status-{user_id}",
            )

    async def _update_live_status(self, context: ContextTypes.DEFAULT_TYPE):
        job = context.job
        data = job.data
        settings = self._bot_settings()
        now = time.monotonic()
        running = self._runner.is_running()
        if (
            now - data["last_edit_at"] < settings["status_refresh_seconds"]
            and (running or data.get("final_pending", False))
        ):
            return
        text = self._live_status_text()
        if text == data["last_text"]:
            data["last_edit_at"] = now
            if not running:
                job.schedule_removal()
                with self._lock:
                    self._live_status_job = None
                    self._live_status_target = None
            return

        delivered = False
        try:
            await context.bot.edit_message_text(
                chat_id=data["chat_id"],
                message_id=data["message_id"],
                text=text,
            )
            delivered = True
            data["last_text"] = text
        except Exception as exc:
            error = str(exc).lower()
            if "message is not modified" in error:
                delivered = True
                data["last_text"] = text
            elif "message to edit not found" in error or "message can't be edited" in error:
                try:
                    try:
                        await context.bot.delete_message(
                            chat_id=data["chat_id"], message_id=data["message_id"]
                        )
                    except Exception:
                        pass
                    replacement = await context.bot.send_message(
                        chat_id=data["chat_id"], text=text
                    )
                    data["message_id"] = replacement.message_id
                    data["last_text"] = text
                    delivered = True
                    with self._lock:
                        if self._live_status_target is not None:
                            self._live_status_target["message_id"] = replacement.message_id
                except Exception as send_error:
                    self._runner.add_log(f"تعذر إرسال تحديث الحالة: {send_error}")
            else:
                self._runner.add_log(f"تعذر تحديث رسالة الحالة مؤقتًا: {exc}")
        data["last_edit_at"] = now
        if not running and delivered:
            data["final_pending"] = False
            job.schedule_removal()
            with self._lock:
                self._live_status_job = None
                self._live_status_target = None
        elif not running:
            data["final_pending"] = True

    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return
        with self._lock:
            active_user_id = self._active_user_id
        if not self._is_admin(update) and update.effective_user.id != active_user_id:
            message = (
                "يوجد تشغيل لمستخدم آخر؛ التفاصيل متاحة له وللمسؤول فقط."
                if self._runner.is_running()
                else "لا يوجد تشغيل مرتبط بحسابك لعرض تفاصيله."
            )
            await update.effective_message.reply_text(message)
            return
        await update.effective_message.reply_text(self._live_status_text())

    async def _stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return
        with self._lock:
            active_user_id = self._active_user_id
        if not self._is_admin(update) and update.effective_user.id != active_user_id:
            await update.effective_message.reply_text("إيقاف التشغيل متاح للمسؤول أو لصاحب التشغيل الحالي فقط.")
            return
        if not self._runner.is_running():
            await update.effective_message.reply_text("لا يوجد تشغيل حاليًا.")
            return
        self._runner.stop()
        await update.effective_message.reply_text(
            "⏹ وصل الإيقاف؛ سيُنهي الطلب الجاري، ثم يحاول تحويل رصيد الفرد المتاح للأونر مرة واحدة قبل التوقف."
        )
