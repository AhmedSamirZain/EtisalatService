"""Admin-only Telegram polling controller for the Streamlit-hosted bot."""

from __future__ import annotations

import asyncio
import threading
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

    def configure(self, token: str, admin_id: int, runner: Any):
        with self._lock:
            if self.is_running() and (token != self._token or admin_id != self._admin_id):
                self._error = "Telegram settings changed; restart the Streamlit app to apply them."
                return
            self._token = token
            self._admin_id = admin_id
            self._runner = runner

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

    def _is_admin(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        with self._lock:
            admin_id = self._admin_id
        return (
            user is not None
            and chat is not None
            and chat.type == "private"
            and admin_id is not None
            and user.id == admin_id
        )

    async def _deny(self, update: Update):
        message = update.effective_message
        if message is not None:
            await message.reply_text("⛔ هذا البوت مخصص للمسؤول المصرّح له فقط.")

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
    def _main_keyboard():
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton(BUTTON_RUN), KeyboardButton(BUTTON_STATUS)],
                [KeyboardButton(BUTTON_STOP), KeyboardButton(BUTTON_CANCEL)],
                [KeyboardButton(BUTTON_HELP), KeyboardButton(BUTTON_HOME)],
            ],
            resize_keyboard=True,
            is_persistent=True,
            input_field_placeholder="اختار أمر من الأزرار",
        )

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        await update.effective_message.reply_text(
            "أهلًا! اختار زر من القائمة: تشغيل، الحالة، إيقاف، أو إلغاء.\n"
            "تقدر كمان تستخدم /run 50000 أو /help.",
            reply_markup=self._main_keyboard(),
        )

    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        await update.effective_message.reply_text(
            "▶️ تشغيل: اكتب الهدف ثم راجع ملخص النظام قبل التأكيد.\n"
            "📊 الحالة: يعرض آخر مرحلة والنظام والرصيد والهدف.\n"
            "⏹ إيقاف: يوقف بعد انتهاء طلب الشبكة الحالي.\n"
            "❌ إلغاء: يلغي إدخال بيانات التشغيل.\n"
            "الأوامر النصية البديلة: /run 50000، /status، /stop، /cancel.",
            reply_markup=self._main_keyboard(),
        )

    async def _run_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return ConversationHandler.END
        if self._runner.is_running():
            await update.effective_message.reply_text("فيه تشغيل قائم بالفعل. اختار الحالة أو أوقفه قبل تشغيل جديد.")
            return ConversationHandler.END
        context.user_data.pop("run_draft", None)
        await update.effective_message.reply_text(
            f"اكتب هدف الميكسات (من 1 إلى {MAX_TARGET_MIX:,})، أو اضغط إلغاء.",
            reply_markup=self._main_keyboard(),
        )
        return TARGET

    async def _run_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
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
        if not self._is_admin(update):
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
        if not self._is_admin(update):
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
        if not self._is_admin(update):
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
        if not self._is_admin(update):
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
        if not self._is_admin(update):
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
        if not self._is_admin(update):
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
        if not self._is_admin(update):
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
            await query.edit_message_text("✅ بدأ التشغيل. استخدم /status للمتابعة و/stop للإيقاف.")
        else:
            await query.edit_message_text("يوجد تشغيل قائم بالفعل؛ لم أبدأ تشغيلًا آخر.")
        return ConversationHandler.END

    async def _cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
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
        if not self._is_admin(update):
            await self._deny(update)
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
        if not self._is_admin(update):
            await self._deny(update)
            return
        if not self._runner.is_running():
            await update.effective_message.reply_text("لا يوجد تشغيل حاليًا.")
            return
        self._runner.stop()
        await update.effective_message.reply_text("⏹ وصل طلب الإيقاف؛ سيتوقف بعد انتهاء طلب الشبكة الحالي.")
