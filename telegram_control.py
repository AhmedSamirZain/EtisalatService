"""Admin-managed Telegram access and polling for the Streamlit-hosted bot."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
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

    def _init_users_db(self):
        self._users_db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._users_db)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS authorized_users ("
                "user_id INTEGER PRIMARY KEY, added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )

    def _authorized_user_ids(self) -> list[int]:
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            rows = db.execute("SELECT user_id FROM authorized_users ORDER BY user_id").fetchall()
        return [int(row[0]) for row in rows]

    def _add_authorized_user(self, user_id: int) -> bool:
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO authorized_users(user_id) VALUES (?)", (user_id,)
            )
            return cursor.rowcount == 1

    def _remove_authorized_user(self, user_id: int) -> bool:
        with self._lock, closing(sqlite3.connect(self._users_db)) as db, db:
            cursor = db.execute("DELETE FROM authorized_users WHERE user_id = ?", (user_id,))
            return cursor.rowcount == 1

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
        application.add_handler(CommandHandler("users", self._users_command))
        application.add_handler(MessageHandler(filters.Regex(r"^👥 لوحة الإدارة$"), self._admin_panel))
        application.add_handler(CallbackQueryHandler(self._admin_callback, pattern=r"^admin:(users|add_help|remove_help)$"))
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
                    text="✅ بوت اتصالات شغّال الآن ويستقبل الأوامر.\nاكتب /admin لفتح لوحة الإدارة أو /start لعرض الأزرار.",
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
            text += "\nلوحة إدارة المستخدمين: /admin."
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
            text += "\n👥 إدارة المستخدمين: /admin."
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
        ])

    async def _admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        await update.effective_message.reply_text(
            "👥 لوحة إدارة المستخدمين\n"
            "إضافة: /adduser ثم رقم Telegram ID.\n"
            "حذف: /removeuser ثم رقم Telegram ID.\n"
            "عرض القائمة: /users.\n\n"
            "كل مستخدم يشغّل ببيانات حساباته بنفسه، وتشغيل واحد فقط مسموح في نفس الوقت.",
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
            lines = [f"• {admin_id} — المسؤول الرئيسي"]
            lines.extend(f"• {user_id}" for user_id in ids if user_id != admin_id)
            await query.edit_message_text(
                "المستخدمون المصرّح لهم:\n" + "\n".join(lines),
                reply_markup=self._admin_keyboard(),
            )
        elif query.data == "admin:add_help":
            await query.edit_message_text(
                "لإضافة شخص: خليه يفتح البوت ويرسل /start، ثم يبعث لك رقم Telegram ID الظاهر له.\n"
                "بعدها أرسل: /adduser 123456789",
                reply_markup=self._admin_keyboard(),
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
                f"✅ تمت إضافة المستخدم {user_id}. اطلب منه إرسال /start للبوت؛ التشغيل متاح له في محادثة خاصة."
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

    async def _users_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        with self._lock:
            admin_id = self._admin_id
        ids = self._authorized_user_ids()
        lines = [f"• {admin_id} — المسؤول الرئيسي"]
        lines.extend(f"• {user_id}" for user_id in ids if user_id != admin_id)
        await update.effective_message.reply_text("المستخدمون المصرّح لهم:\n" + "\n".join(lines))

    async def _run_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await self._deny(update)
            return ConversationHandler.END
        if self._runner.is_running():
            await update.effective_message.reply_text("فيه تشغيل قائم بالفعل. اختار الحالة أو أوقفه قبل تشغيل جديد.")
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
        try:
            started = self._runner.start(
                draft["owner_email"],
                draft["owner_password"],
                draft["child_email"],
                draft["child_password"],
                int(draft["target"]),
            )
        except (KeyError, TypeError, ValueError):
            await query.edit_message_text("انتهت بيانات الطلب أو لم تكتمل. ابدأ من جديد باستخدام /run.")
            return ConversationHandler.END
        if started:
            with self._lock:
                self._active_user_id = update.effective_user.id
            await query.edit_message_text("✅ بدأ التشغيل. استخدم /status للمتابعة و/stop للإيقاف.")
        else:
            await query.edit_message_text("يوجد تشغيل قائم بالفعل؛ لم أبدأ تشغيلًا آخر.")
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
        logs, error = self._runner.snapshot()
        state = "🟢 يعمل" if self._runner.is_running() else "⚪ متوقف"
        text = f"حالة التشغيل: {state}"
        if error:
            text += f"\nآخر خطأ: {error}"
        if logs:
            text += "\n\nآخر السجل:\n" + "\n".join(logs[-8:])
        await update.effective_message.reply_text(text[-3800:])

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
        await update.effective_message.reply_text("⏹ وصل طلب الإيقاف؛ سيتوقف بعد انتهاء طلب الشبكة الحالي.")
