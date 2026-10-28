"""Streamlit control panel for the Etisalat mix automation script."""

from __future__ import annotations

import hmac
import importlib.util
import threading
from collections import deque
from pathlib import Path

import streamlit as st


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
    """One in-process worker shared by Streamlit sessions."""

    def __init__(self):
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
        bot = load_bot_module()
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
            self.add_log("⏹ طُلب إيقاف البوت؛ سيقف بعد انتهاء الطلب الحالي.")

    def _run(self, bot, owner_email, owner_password, child_email, child_password, target):
        bot.LOG_SINK = self.add_log
        try:
            self.add_log("بدء تسجيل الدخول إلى حساب الأونر...")
            owner = bot.EtisalatAPI()
            if not owner.login(owner_email, owner_password):
                raise RuntimeError("فشل تسجيل دخول الأونر. راجع البيانات أو حالة الحساب.")
            self.add_log(f"تم تسجيل دخول الأونر — Dial: {owner.current_dial}")

            if self._stop_requested:
                return

            self.add_log("بدء تسجيل الدخول إلى حساب الفرد...")
            child = bot.EtisalatAPI()
            if not child.login(child_email, child_password):
                raise RuntimeError("فشل تسجيل دخول الفرد. راجع البيانات أو حالة الحساب.")
            self.add_log(f"تم تسجيل دخول الفرد — Dial: {child.current_dial}")

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
            self.add_log(f"❌ خطأ: {exc}")
        finally:
            bot.LOG_SINK = None
            with self._lock:
                self._loop = None


@st.cache_resource
def get_runner():
    return BackgroundRunner()


def require_secrets():
    required = (
        "APP_ACCESS_PASSWORD",
        "OWNER_EMAIL",
        "OWNER_PASSWORD",
        "CHILD_EMAIL",
        "CHILD_PASSWORD",
    )
    missing = [key for key in required if not st.secrets.get(key)]
    if missing:
        st.error("إعداد الأسرار غير مكتمل. أضف المفاتيح المطلوبة في Streamlit Secrets.")
        st.code("\n".join(missing))
        st.info("راجع ملف `.streamlit/secrets.toml.example` لمعرفة شكل الإعداد.")
        st.stop()
    return {key: str(st.secrets[key]) for key in required}


def authenticate(app_password):
    if st.session_state.get("authenticated"):
        return True
    st.subheader("دخول لوحة التحكم")
    with st.form("access_form"):
        entered = st.text_input("كلمة مرور لوحة التحكم", type="password")
        submitted = st.form_submit_button("دخول", type="primary")
    if submitted:
        if hmac.compare_digest(entered, app_password):
            st.session_state.authenticated = True
            st.rerun()
        st.error("كلمة المرور غير صحيحة.")
    return False


st.set_page_config(page_title="Etisalat Mix Bot", page_icon="📶", layout="wide")
st.title("📶 لوحة تشغيل بوت ميكسات اتصالات")
st.caption("تعمل واجهة التحكم بينما ينفّذ العامل الدورة في الخلفية داخل عملية Streamlit.")

secrets = require_secrets()
if not authenticate(secrets["APP_ACCESS_PASSWORD"]):
    st.stop()

st.warning(
    "التشغيل ينفّذ عمليات حقيقية على الحساب: تجديد باقة، تغيير نظام، وتحويل ميكسات. "
    "تأكد من صلاحية الحسابات والتكلفة قبل البدء."
)

runner = get_runner()

@st.fragment(run_every="2s")
def live_panel():
    with st.form("run_form"):
        target = st.number_input("هدف الميكسات", min_value=1, value=50000, step=5000)
        confirmed = st.checkbox("أؤكد أنني راجعت الحسابات وأوافق على العمليات الفعلية")
        start_clicked = st.form_submit_button(
            "تشغيل البوت في الخلفية",
            type="primary",
            disabled=runner.is_running(),
        )

    if start_clicked:
        if not confirmed:
            st.error("علّم مربع التأكيد قبل التشغيل.")
        elif runner.start(
            secrets["OWNER_EMAIL"],
            secrets["OWNER_PASSWORD"],
            secrets["CHILD_EMAIL"],
            secrets["CHILD_PASSWORD"],
            int(target),
        ):
            st.success("بدأ العامل في الخلفية. اترك التطبيق يعمل لمتابعة الدورة.")
        else:
            st.info("يوجد تشغيل قائم بالفعل.")

    running = runner.is_running()
    st.subheader("حالة التشغيل")
    st.write("🟢 يعمل في الخلفية" if running else "⚪ متوقف")
    if running and st.button("إيقاف بعد الطلب الحالي", key="stop_bot"):
        runner.stop()
    logs, error = runner.snapshot()
    if error:
        st.error(error)
    st.subheader("السجل")
    if logs:
        st.code("\n".join(logs[-250:]), language=None)
    else:
        st.caption("لا توجد رسائل بعد.")


live_panel()
with st.expander("ملاحظات مهمة"):
    st.markdown(
        "- بيانات الحسابات وكلمة مرور اللوحة تُقرأ من `st.secrets` ولا تظهر في الواجهة.\n"
        "- لا ترفع ملف `secrets.toml` أو بيانات حساباتك إلى GitHub.\n"
        "- هذا المشروع ليس بوت Telegram: لا يوجد في الكود الحالي استخدام لـ `BOT_TOKEN` أو `ADMIN_ID`.\n"
        "- العامل يعمل داخل عملية Streamlit؛ إذا أُعيد تشغيل التطبيق أو نامت الاستضافة فسيتوقف.\n"
        "- كلمة مرور اللوحة طبقة حماية بسيطة، وليست بديلًا عن تقييد الوصول للتطبيق من إعدادات الاستضافة."
    )
