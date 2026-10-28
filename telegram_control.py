"""Admin-only Telegram polling controller for the Streamlit-hosted bot."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)


MAX_TARGET_MIX = 1_000_000


class TelegramControlBot:
    """Run Telegram long polling in a managed background thread."""

    def __init__(self):
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._token: str | None = None
        self._admin_id: int | None = None
        self._runner: Any = None
        self._credentials: dict[str, str] = {}
        self._error: str | None = None

    def configure(self, token: str, admin_id: int, runner: Any, credentials: dict[str, str]):
        with self._lock:
            if self.is_running() and (token != self._token or admin_id != self._admin_id):
                self._error = "Telegram settings changed; restart the Streamlit app to apply them."
                return
            self._token = token
            self._admin_id = admin_id
            self._runner = runner
            self._credentials = dict(credentials)

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
        application.add_handler(CommandHandler("start", self._start_command))
        application.add_handler(CommandHandler("help", self._help_command))
        application.add_handler(CommandHandler("run", self._run_command))
        application.add_handler(CommandHandler("status", self._status_command))
        application.add_handler(CommandHandler("stop", self._stop_command))
        application.add_handler(CallbackQueryHandler(self._confirmation, pattern=r"^(run:\d+|cancel)$"))

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

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        await update.effective_message.reply_text(
            "أهلًا! بوت التحكم جاهز.\n"
            "الأوامر: /run <عدد_الميكسات>، /status، /stop، /help"
        )

    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        await update.effective_message.reply_text(
            "/run 50000 — يطلب تأكيدًا قبل بدء دورة اتصالات ذات عمليات فعلية.\n"
            "/status — يعرض حالة التشغيل وآخر السجل.\n"
            "/stop — يطلب إيقاف الدورة بعد انتهاء طلب الشبكة الحالي.\n"
            "/help — يعرض هذه المساعدة."
        )

    async def _run_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        message = update.effective_message
        if not context.args:
            await message.reply_text(f"استخدم: /run 50000 (الحد الأقصى {MAX_TARGET_MIX:,})")
            return
        try:
            target = int(context.args[0].replace(",", ""))
        except ValueError:
            await message.reply_text("الهدف لازم يكون رقمًا صحيحًا من الميكسات.")
            return
        if not 1 <= target <= MAX_TARGET_MIX:
            await message.reply_text(f"الهدف لازم يكون بين 1 و{MAX_TARGET_MIX:,} ميكس.")
            return
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ تأكيد التشغيل", callback_data=f"run:{target}"),
                InlineKeyboardButton("إلغاء", callback_data="cancel"),
            ]]
        )
        await message.reply_text(
            f"⚠️ سيبدأ تشغيلًا قد يجدد باقات ويحوّل ميكسات. الهدف: {target:,}.\nهل تؤكد؟",
            reply_markup=keyboard,
        )

    async def _confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not self._is_admin(update):
            await query.answer("غير مصرح", show_alert=True)
            return
        await query.answer()
        if query.data == "cancel":
            await query.edit_message_text("تم الإلغاء.")
            return
        target = int(query.data.split(":", 1)[1])
        with self._lock:
            runner = self._runner
            credentials = dict(self._credentials)
        started = runner.start(
            credentials["OWNER_EMAIL"],
            credentials["OWNER_PASSWORD"],
            credentials["CHILD_EMAIL"],
            credentials["CHILD_PASSWORD"],
            target,
        )
        if started:
            await query.edit_message_text(f"✅ بدأ التشغيل بهدف {target:,} ميكس. استخدم /status للمتابعة و/stop للإيقاف.")
        else:
            await query.edit_message_text("يوجد تشغيل قائم بالفعل؛ لم أبدأ تشغيلًا آخر.")

    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        with self._lock:
            runner = self._runner
        logs, error = runner.snapshot()
        state = "🟢 يعمل" if runner.is_running() else "⚪ متوقف"
        text = f"الحالة: {state}"
        if error:
            text += f"\nآخر خطأ: {error}"
        if logs:
            recent = "\n".join(logs[-8:])
            text += f"\n\nآخر السجل:\n{recent}"
        await update.effective_message.reply_text(text[-3800:])

    async def _stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return
        with self._lock:
            runner = self._runner
        if not runner.is_running():
            await update.effective_message.reply_text("البوت متوقف حاليًا.")
            return
        runner.stop()
        await update.effective_message.reply_text("⏹ وصل طلب الإيقاف؛ سيتوقف بعد انتهاء الطلب الجاري.")
