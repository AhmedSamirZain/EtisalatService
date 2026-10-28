"""Streamlit host/status page for the admin-only Telegram bot."""

from __future__ import annotations

import importlib.util
import threading
from collections import deque
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from telegram_control import TelegramControlBot


ROOT = Path(__file__).resolve().parent
BOT_FILE = ROOT / "etisalat mix.py"


@st.cache_resource
def load_bot_module():
    spec = importlib.util.spec_from_file_location("etisalat_mix_bot", BOT_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load bot script: {BOT_FILE.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackgroundRunner:
    """Run one automation task at a time using credentials supplied per run."""

    def __init__(self, bot_module):
        self._bot_module = bot_module
        self._lock = threading.RLock()
        self._logs = deque(maxlen=1000)
        self._thread = None
        self._loop = None
        self._stop_requested = False
        self._error = None

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def add_log(self, message):
        with self._lock:
            self._logs.append(str(message))

    def snapshot(self):
        with self._lock:
            return list(self._logs), self._error

    def start(self, owner_email, owner_password, child_email, child_password, target):
        bot = self._bot_module
        with self._lock:
            if self.is_running():
                return False
            self._logs.clear()
            self._error = None
            self._stop_requested = False
            self._loop = None
            self._thread = threading.Thread(
                target=self._run,
                args=(bot, owner_email, owner_password, child_email, child_password, target),
                name="etisalat-auto-loop",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self):
        with self._lock:
            self._stop_requested = True
            if self._loop is not None:
                self._loop.stop()
            self.add_log("⏹ طُلب إيقاف التشغيل؛ سيقف بعد انتهاء طلب الشبكة الحالي.")

    def _run(self, bot, owner_email, owner_password, child_email, child_password, target):
        bot.LOG_SINK = self.add_log
        try:
            self.add_log("بدء تسجيل الدخول إلى حساب الأونر...")
            owner = bot.EtisalatAPI()
            if not owner.login(owner_email, owner_password):
                raise RuntimeError("فشل تسجيل دخول الأونر. راجع البيانات أو حالة الحساب.")
            self.add_log("تم تسجيل دخول الأونر.")

            if self._stop_requested:
                return

            self.add_log("بدء تسجيل الدخول إلى حساب الفرد...")
            child = bot.EtisalatAPI()
            if not child.login(child_email, child_password):
                raise RuntimeError("فشل تسجيل دخول الفرد. راجع البيانات أو حالة الحساب.")
            self.add_log("تم تسجيل دخول الفرد.")

            if self._stop_requested:
                return

            loop = bot.AutoLoop(owner, child, target)
            loop.owner_email = owner_email
            loop.owner_pass = owner_password
            with self._lock:
                self._loop = loop
                if self._stop_requested:
                    loop.stop()
            loop.run()
            self.add_log("انتهى تشغيل الدورة.")
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
            self.add_log(f"❌ خطأ أثناء التشغيل: {exc}")
        finally:
            bot.LOG_SINK = None
            with self._lock:
                self._loop = None


@st.cache_resource
def get_runner():
    return BackgroundRunner(load_bot_module())


@st.cache_resource
def get_telegram_controller():
    return TelegramControlBot()


def require_secrets():
    required = ("TELEGRAM_BOT_TOKEN", "ADMIN_ID")
    try:
        configured = st.secrets.to_dict()
    except StreamlitSecretNotFoundError:
        configured = {}
    missing = [key for key in required if not configured.get(key)]
    if missing:
        st.error("أضف أسرار Telegram المطلوبة في إعدادات Streamlit.")
        st.code("\n".join(missing))
        st.info("استخدم شكل الإعداد الموجود في `.streamlit/secrets.toml.example`.")
        st.stop()
    return {key: str(configured[key]) for key in required}


st.set_page_config(page_title="Etisalat Telegram Bot", page_icon="📶", layout="centered")
st.title("📶 بوت ميكسات اتصالات على Telegram")
st.caption("تُدخل بيانات حسابات اتصالات داخل محادثة البوت عند كل تشغيل؛ لا تحتاج إضافتها إلى Secrets.")

secrets = require_secrets()
try:
    admin_id = int(secrets["ADMIN_ID"])
except ValueError:
    st.error("قيمة ADMIN_ID لازم تكون رقم Telegram User ID صحيحًا.")
    st.stop()

runner = get_runner()
telegram_control = get_telegram_controller()
telegram_control.configure(
    token=secrets["TELEGRAM_BOT_TOKEN"],
    admin_id=admin_id,
    runner=runner,
)
telegram_control.start()

@st.fragment(run_every="3s")
def status_panel():
    polling, _ = telegram_control.status()
    st.subheader("حالة اتصال Telegram")
    st.write("🟢 البوت متصل ويستقبل الأوامر" if polling else "🟠 البوت يحاول الاتصال أو متوقف")
    if telegram_error:
        st.error("تعذّر الاتصال. راجع TELEGRAM_BOT_TOKEN وسجل تشغيل Streamlit.")

    running = runner.is_running()
    st.subheader("حالة دورة اتصالات")
    st.write("🟢 يوجد تشغيل" if running else "⚪ لا يوجد تشغيل")


status_panel()
st.warning(
    "أوامر التشغيل متاحة فقط للمسؤول المحدد في ADMIN_ID، ومن محادثة خاصة. "
    "التشغيل قد يجدد باقات ويحوّل ميكسات، وهذه عمليات فعلية."
)
st.info("في Telegram: أرسل /start ثم /help. لبدء دورة استخدم /run 50000.")
st.caption(
    "استضافة Streamlit قد تنام أو تعيد التشغيل؛ عندها يتوقف البوت والدورة. "
    "للتشغيل الدائم 24/7 استخدم استضافة عامل تعمل باستمرار."
)
