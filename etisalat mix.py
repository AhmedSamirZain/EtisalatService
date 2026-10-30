
import requests
import time
import xml.etree.ElementTree as ET
import urllib.parse
import hashlib
import base64
import json
import sys
import os

# ============================================================
# 1) الإعدادات
# ============================================================

BASE_URL  = "https://mab.etisalat.com.eg:11003"
DEVICE_ID = "b61c90c58c612671"

PLAN_IDS = {
    "Hekaya_XRP_MIX_Parent_7": ("حكاية ميكسات 270", "270"),
    "Hekaya_XRP_MIX_Parent_6": ("حكاية ميكسات 180", "180"),
    "Hekaya_XRP_MIX_Parent_5": ("حكاية ميكسات 240", "240"),
    "Hekaya_XRP_MIX_Parent_8": ("حكاية ميكسات 52",  "52"),
}


# ============================================================
# 2) أدوات الطباعة
# ============================================================

class C:
    """ألوان التيرمنال"""
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    GRAY   = "\033[90m"


# Optional callback used by the Streamlit UI to display background-thread logs.
LOG_SINK = None


def log(msg, color=C.RESET, prefix=""):
    formatted = f"{color}{prefix}{msg}{C.RESET}"
    if LOG_SINK is not None:
        LOG_SINK(f"{prefix}{msg}")
    else:
        print(formatted)


def log_ok(msg):    log(f"✅ {msg}", C.GREEN)
def log_err(msg):   log(f"❌ {msg}", C.RED)
def log_info(msg):  log(f"ℹ️  {msg}", C.CYAN)
def log_warn(msg):  log(f"⚠️  {msg}", C.YELLOW)
def log_step(msg): log(f"\n▶ {msg}", C.BOLD + C.BLUE)


def read_status_until_available(api, should_stop, report, purpose):
    """Single safe status gate shared by preflight, cycle, and transfer decisions."""
    attempt = 0
    while not should_stop():
        if attempt < 6:
            pause, timeout, mode = 0.25, (2, 3), "سريع"
        elif attempt < 12:
            pause, timeout, mode = 0.75, (3, 5), "متوسط"
        else:
            pause, timeout, mode = 2.0, (3, 10), "مستمر"

        status = api.get_status_and_child(timeout=timeout)
        if should_stop():
            return None
        if status[0] is not None:
            if attempt:
                report(f"تمت قراءة {purpose} بنجاح بعد {attempt + 1} محاولة.")
            return status

        attempt += 1
        if attempt == 1 or attempt % 5 == 0:
            report(
                f"تعذرت قراءة {purpose} مؤقتاً ({attempt} محاولة، وضع {mode})؛ "
                "لا توجد عمليات حسابية أثناء الانتظار."
            )

        deadline = time.monotonic() + pause
        while not should_stop():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))
    return None


def banner():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════╗
║   🚀  Auto Loop — ميكسات اتصالات                ║
║   Terminal Version v1.0                          ║
╚══════════════════════════════════════════════════╝{C.RESET}
""")


# ============================================================
# 3) API اتصالات
# ============================================================

class EtisalatAPI:
    def __init__(self):
        self.session = requests.Session()
        self.auth_token = None
        self.current_dial = None
        self.base_headers = {
            "applicationVersion": "2", "applicationName": "MAB",
            "Accept": "text/xml", "Language": "ar",
            "APP-BuildNumber": "10744", "APP-Version": "36.1.0",
            "OS-Type": "Android", "OS-Version": "16", "APP-STORE": "GOOGLE",
            "C-Type": "WIFI", "Is-Corporate": "false",
            "User-Agent": "okhttp/5.0.0-alpha.11",
            "Content-Type": "text/xml; charset=UTF-8",
        }

    def _sign(self, url_path, body_xml):
        h = hashlib.sha256(f"{url_path}{DEVICE_ID}".encode()).hexdigest()
        u = hashlib.sha256(url_path.encode()).hexdigest()
        b = hashlib.sha256(body_xml.encode()).hexdigest() if body_xml else ""
        return h, u, b

    def login(self, email, password):
        email = (email or "").strip()
        password = (password or "").strip()
        if not email or not password:
            return False
        auth_b64 = base64.b64encode(f"{email}:{password}".encode()).decode()
        for attempt in range(3):
            headers = self.base_headers.copy()
            headers["Authorization"] = f"Basic {auth_b64}"
            login_xml = (
                "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
                "<loginRequest>"
                f"<deviceId>{DEVICE_ID}</deviceId>"
                "<firstLoginAttempt>true</firstLoginAttempt>"
                "<modelType>CPH2737</modelType><osVersion>16</osVersion>"
                f"<platform>Android</platform><udid>{DEVICE_ID}</udid>"
                "</loginRequest>"
            )
            url_path = "/Saytar/rest/authentication/loginWithPlan"
            h, u, b = self._sign(url_path, login_xml)
            headers.update({"headerSignature": h, "urlSignature": u, "bodySignature": b})
            try:
                r = self.session.post(BASE_URL + url_path, headers=headers,
                                      data=login_xml, timeout=15)
                if r.status_code == 200 and "<status>true</status>" in r.text:
                    self.auth_token = r.headers.get("auth")
                    if self.auth_token:
                        self.base_headers["auth"] = f"Bearer {self.auth_token}"
                        try:
                            root = ET.fromstring(r.text)
                            d = root.find(".//dial")
                            self.current_dial = d.text if d is not None else None
                        except Exception:
                            self.current_dial = None
                        if not self.current_dial:
                            try:
                                tp = self.auth_token.split(".")[1]
                                tp += "=" * (-len(tp) % 4)
                                payload = json.loads(base64.b64decode(tp))
                                self.current_dial = payload.get("selectedDial")
                            except Exception:
                                pass
                        return True
            except Exception as e:
                log_warn(f"محاولة {attempt+1}/3 فشلت: {e}")
            if attempt < 2:
                time.sleep(5)
        return False

    def get_status_and_child(self, timeout=10):
        if not self.current_dial:
            return None, 0, None
        xml_body = (
            "<dialAndLanguageRequest><segmentId>XRP</segmentId>"
            f"<subscriberNumber>{self.current_dial}</subscriberNumber>"
            "<language>1</language><shortCode>ALFTL</shortCode>"
            "</dialAndLanguageRequest>"
        )
        enc = urllib.parse.quote(xml_body)
        url_path = f"/Saytar/rest/servicemanagement/getGenericConsumptionsV2?requestParam={enc}"
        headers = self.base_headers.copy()
        h, u, _ = self._sign(url_path, "")
        headers.update({"headerSignature": h, "urlSignature": u})
        try:
            r = self.session.get(BASE_URL + url_path, headers=headers, timeout=timeout)
            if r.status_code != 200:
                return None, 0, None
            root = ET.fromstring(r.text)
            p_id_elem = root.find(".//ratePlanConsumption/productId")
            p_id = p_id_elem.text if p_id_elem is not None else "Unknown"

            r_mix = 0
            for cons in root.findall(".//newMeterConsumption/consumptionList/consumption"):
                ut = (cons.findtext("unitType") or "").upper()
                prod = (cons.findtext("productId") or "").upper()
                if "UNITS" in ut or "MIX" in ut or "XRP" in prod:
                    try:
                        r_mix = int(cons.findtext("remainingValue", "0") or 0)
                    except ValueError:
                        r_mix = 0
                    break
            if r_mix == 0:
                vals = []
                for cons in root.findall(".//newMeterConsumption/consumptionList/consumption"):
                    try:
                        vals.append(int(cons.findtext("remainingValue", "0") or 0))
                    except Exception:
                        pass
                if vals:
                    r_mix = max(vals)

            child_number = None
            for item in root.findall(".//fafNumberList/fafNumber/value"):
                if item.text != self.current_dial:
                    child_number = item.text
            return p_id, r_mix, child_number
        except Exception:
            # The shared retry gate reports transient failures without spamming logs.
            return None, 0, None

    def change_plan(self, plan_name, plan_id=""):
        xml_body = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
            "<migrateRequest><addonName></addonName><oMSRatePlan>true</oMSRatePlan>"
            "<operation>FULL_MIGRATE</operation>"
            f"<productName>{plan_name}</productName>"
            f"<ratePlanID>{plan_id}</ratePlanID>"
            f"<subscriberNumber>{self.current_dial}</subscriberNumber>"
            "</migrateRequest>"
        )
        url_path = "/Saytar/rest/HekayaPartial/migrate"
        headers = self.base_headers.copy()
        h, u, b = self._sign(url_path, xml_body)
        headers.update({"headerSignature": h, "urlSignature": u, "bodySignature": b})
        try:
            r = self.session.post(BASE_URL + url_path, headers=headers,
                                  data=xml_body, timeout=10)
            return r.status_code == 200 and "<status>true</status>" in r.text
        except Exception as e:
            log_warn(f"change_plan: {e}")
            return False

    def renew_bundle_action(self, plan_name, amount):
        xml_body = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
            "<generalSubmitOrderRequest>"
            f"<msisdn>{self.current_dial}</msisdn>"
            "<operation>FULL_RENEW</operation><parameters />"
            "<paymentOptions><paymentOption>"
            f"<amount>{amount}</amount><optionID>BALANCE</optionID>"
            "</paymentOption></paymentOptions>"
            f"<productName>{plan_name}</productName>"
            "<requestId>ALFA_PAYMENT_OPTIONS</requestId>"
            "</generalSubmitOrderRequest>"
        )
        url_path = "/Saytar/rest/General/submitOrder"
        headers = self.base_headers.copy()
        h, u, b = self._sign(url_path, xml_body)
        headers.update({"headerSignature": h, "urlSignature": u, "bodySignature": b})
        try:
            r = self.session.post(BASE_URL + url_path, headers=headers,
                                  data=xml_body, timeout=10)
            return r.status_code == 200
        except Exception as e:
            log_warn(f"renew: {e}")
            return False

    def transfer_mix(self, receiver_number, quota_amount, product_name):
        try:
            quota = int(quota_amount)
        except Exception:
            return False
        xml_body = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
            "<newFamilyTransferRequest><distribute>false</distribute>"
            f"<msisdn>{self.current_dial}</msisdn>"
            f"<productName>{product_name}</productName>"
            f"<quota>{quota}</quota>"
            f"<receiverMsisdn>{receiver_number}</receiverMsisdn>"
            f"<senderMsisdn>{self.current_dial}</senderMsisdn>"
            "<type>UNITS</type></newFamilyTransferRequest>"
        )
        url_path = "/Saytar/rest/services/newTransfer/v2"
        headers = self.base_headers.copy()
        h, u, b = self._sign(url_path, xml_body)
        headers.update({"headerSignature": h, "urlSignature": u, "bodySignature": b})
        try:
            r = self.session.post(BASE_URL + url_path, headers=headers,
                                  data=xml_body, timeout=10)
            return r.status_code == 200 and "<status>true</status>" in r.text
        except Exception as e:
            log_warn(f"transfer: {e}")
            return False


# ============================================================
# 4) الدورة التلقائية
# ============================================================

class AutoLoop:
    def __init__(
        self,
        owner: EtisalatAPI,
        child: EtisalatAPI,
        target_mix: int,
        retry_settings_provider=None,
        status_callback=None,
    ):
        self.owner = owner
        self.child = child
        self.target_mix = target_mix
        self.cycle_count = 0
        self.stopped = False
        self.stop_requested = False
        self.outcome = "failed"
        self._stop_child_transfer_attempted = False
        self._stop_child_transfer_accepted = False
        self._stop_child_transfer_amount = 0
        self._retry_settings_provider = retry_settings_provider
        self._status_callback = status_callback
        self._live_state = {"phase": "starting", "status": "بدء التشغيل", "target_mix": target_mix}

    def _set_live_state(self, **updates):
        self._live_state.update(updates)
        if self._status_callback is not None:
            try:
                self._status_callback(dict(self._live_state))
            except Exception:
                pass

    def _retry_settings(self):
        try:
            settings = self._retry_settings_provider() if self._retry_settings_provider else {}
        except Exception:
            settings = {}
        interval = max(60, int(settings.get("plan_retry_seconds", 240)))
        retry_limit = max(0, int(settings.get("plan_retry_limit", 3)))
        return interval, retry_limit

    def stop(self):
        """Request a safe stop; final child-to-owner cleanup runs before exit."""
        self.stop_requested = True
        self._set_live_state(phase="stopping", status="وصل طلب الإيقاف؛ جارٍ إنهاء الطلب الحالي")

    def _sleep(self, seconds):
        """Interruptible sleep used between operations and during retry backoff."""
        deadline = time.monotonic() + max(0.0, float(seconds))
        while not self.stopped and not self.stop_requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(0.1, remaining))
        return False

    def _read_status(self, api, purpose):
        return read_status_until_available(
            api, lambda: self.stopped or self.stop_requested, log_info, purpose
        )

    def _read_initial_status(self):
        return self._read_status(self.owner, "النظام والرصيد الحاليين")

    def _read_child_number(self):
        """Require a child number, not just a successful status request, before operations."""
        readable_misses = 0
        while not self.stopped and not self.stop_requested:
            status = self._read_status(self.owner, "بيانات الفرد")
            if status is None:
                return None
            _, _, child_number = status
            if child_number:
                log_ok(f"تم تأكيد الفرد: {child_number}")
                return child_number

            readable_misses += 1
            if readable_misses >= 3:
                return None
            log_info(
                f"قراءة الحالة نجحت لكن بيانات الفرد غير ظاهرة ({readable_misses}/3). "
                "سأعيد الفحص قبل إيقاف التشغيل."
            )
            if not self._sleep(min(2.0, 0.25 * (2 ** (readable_misses - 1)))):
                return None
        return None

    def _wait_for_plan(self, expected_plan_id, previous_plan_id=None, target_pid=""):
        """Wait for exact plan confirmation; retry only after a readable old-plan response."""
        interval_seconds, retry_limit = self._retry_settings()
        request_started = time.monotonic()
        readable_checks = 0
        limit_notice_sent = False
        last_unexpected_plan = None
        self._set_live_state(
            phase="plan_confirmation",
            status="بانتظار تأكيد النظام المطلوب",
            current_plan_id=previous_plan_id,
            target_plan_id=expected_plan_id,
            retries_done=0,
            retry_limit=retry_limit,
            retry_interval_seconds=interval_seconds,
            request_started=request_started,
            last_status_read=None,
        )
        while not self.stopped and not self.stop_requested:
            if not self._sleep(3):
                return False
            status = self._read_status(self.owner, f"تأكيد النظام {expected_plan_id}")
            if status is None or self.stopped or self.stop_requested:
                return False

            current_plan = status[0]
            now = time.monotonic()
            readable_checks += 1
            interval_seconds, retry_limit = self._retry_settings()
            retries_done = self._live_state.get("retries_done", 0)
            self._set_live_state(
                current_plan_id=current_plan,
                last_status_read=now,
                retry_limit=retry_limit,
                retry_interval_seconds=interval_seconds,
            )
            if current_plan == expected_plan_id:
                self._set_live_state(
                    phase="plan_confirmed",
                    status="تم تأكيد النظام المطلوب",
                    current_plan_id=current_plan,
                    target_plan_id=expected_plan_id,
                    retries_done=retries_done,
                )
                log_ok(f"تم تأكيد النظام المطلوب بالضبط: {current_plan}")
                return True

            if current_plan == previous_plan_id:
                elapsed = now - request_started
                if elapsed >= interval_seconds:
                    if retry_limit and retries_done >= retry_limit:
                        self._set_live_state(
                            phase="plan_retry_limit",
                            status="لم يتأكد تغيير النظام؛ اكتمل حد الإعادات وسأواصل المراقبة",
                        )
                        if not limit_notice_sent:
                            log_warn(
                                f"ما زال النظام {previous_plan_id} بعد {interval_seconds // 60} دقيقة، "
                                f"ووصلت لحد الإعادات ({retry_limit})؛ سأواصل المراقبة دون طلب إضافي."
                            )
                            limit_notice_sent = True
                    else:
                        if self.stop_requested:
                            return False
                        retry_number = retries_done + 1
                        self._set_live_state(
                            phase="plan_retrying",
                            status=f"النظام ما زال قديمًا؛ إعادة طلب التغيير ({retry_number})",
                            retry_number=retry_number,
                        )
                        limit_label = "بلا حد" if retry_limit == 0 else str(retry_limit)
                        log_warn(
                            f"قراءة ناجحة أكدت أن النظام ما زال {previous_plan_id} بعد "
                            f"{interval_seconds // 60} دقيقة؛ إعادة طلب تغيير النظام "
                            f"إلى {expected_plan_id} ({retry_number} من {limit_label})."
                        )
                        accepted = self.owner.change_plan(expected_plan_id, target_pid)
                        request_started = time.monotonic()
                        retries_done = retry_number
                        self._set_live_state(
                            phase="plan_confirmation",
                            status=(
                                "اتصالات قبلت طلب إعادة التغيير؛ بانتظار تأكيد النظام"
                                if accepted
                                else "رد إعادة طلب التغيير غير مؤكد؛ أتحقق قبل أي إعادة أخرى"
                            ),
                            retries_done=retries_done,
                            retry_number=None,
                            request_started=request_started,
                            current_plan_id=current_plan,
                        )
                        if accepted:
                            log_info("اتصالات قبلت طلب إعادة تغيير النظام؛ سأنتظر التأكيد قبل أي خطوة أخرى.")
                        else:
                            log_warn("رد إعادة تغيير النظام غير مؤكد؛ لن أكرر الطلب حتى يحين الفاصل وتؤكد قراءة ناجحة بقاء النظام القديم.")
                        limit_notice_sent = False
                else:
                    self._set_live_state(
                        phase="plan_confirmation",
                        status="النظام الحالي ما زال القديم؛ أنتظر مهلة إعادة الطلب",
                    )
            else:
                self._set_live_state(
                    phase="plan_confirmation",
                    status="قراءة الحالة نجحت لكن النظام غير متوقع؛ أواصل التحقق دون إعادة الطلب",
                )
                if current_plan != last_unexpected_plan:
                    log_warn(
                        f"قراءة النظام الحالية ({current_plan}) لا تطابق النظام المطلوب "
                        f"({expected_plan_id}) أو السابق ({previous_plan_id})؛ لن أعيد الطلب على حالة غير مؤكدة."
                    )
                    last_unexpected_plan = current_plan

            if readable_checks == 1 or readable_checks % 10 == 0:
                mode = "بلا حد" if retry_limit == 0 else str(retry_limit)
                log_info(
                    f"ما زلت أتحقق من {expected_plan_id}؛ النظام المقروء {current_plan}، "
                    f"الإعادات {retries_done}/{mode}."
                )
        return False

    def run(self):
        try:
            self._run()
        except KeyboardInterrupt:
            self.stop_requested = True
            log_warn("\nتم طلب الإيقاف؛ سأحاول تحويل رصيد الفرد المتاح للأونر قبل الخروج.")
        except Exception as e:
            log_err(f"خطأ غير متوقع: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.stop_requested:
                try:
                    self._cleanup_after_stop()
                except Exception as e:
                    log_warn(f"تعذر إكمال تحويل الإيقاف: {e}")
                self.outcome = "stopped"
            self.stopped = True

    def _cleanup_after_stop(self):
        """Make one bounded attempt to sweep the currently available child balance to the owner."""
        log_warn("جارٍ الإيقاف الآمن: لن أبدأ تجديدًا أو تغيير نظام جديد.")
        if self._stop_child_transfer_attempted:
            if self._stop_child_transfer_accepted:
                log_ok(
                    f"طلب تحويل الفرد للأونر ({self._stop_child_transfer_amount:,} ميكس) قُبل بالفعل؛ "
                    "لن أعيده أو أنتظر رصيد الإرجاع."
                )
                return
            verification = self.child.get_status_and_child(timeout=(2, 4))
            if verification[0] is not None:
                balance = max(0, int(verification[1] or 0))
                if balance < self._stop_child_transfer_amount:
                    log_ok(f"تغير رصيد الفرد إلى {balance:,} بعد طلب التحويل؛ لن أكرر الطلب.")
                else:
                    log_warn("طلب تحويل الفرد السابق غير مؤكد؛ لن أكرره لتجنب تحويل مزدوج.")
            else:
                log_warn("تعذر تأكيد طلب تحويل الفرد السابق؛ لن أعيده لتجنب تحويل مزدوج.")
            return
        if not getattr(self.owner, "current_dial", None):
            log_warn("لا يمكن تنفيذ تحويل الإيقاف لأن رقم الأونر غير متاح.")
            return

        status = None
        for attempt in range(3):
            status = self.child.get_status_and_child(timeout=(2, 4))
            if status[0] is not None:
                break
            if attempt < 2:
                log_info(f"قراءة رصيد الفرد للإيقاف لم تنجح؛ إعادة محدودة {attempt + 1}/2.")
                time.sleep(0.4 * (attempt + 1))

        if status is None or status[0] is None:
            log_warn("لم أستطع قراءة رصيد الفرد بعد 3 محاولات؛ أوقفت التشغيل دون إعادة أي طلب تحويل.")
            return

        child_balance = max(0, int(status[1] or 0))
        if child_balance == 0:
            log_info("رصيد الفرد الحالي صفر؛ لن أنتظر ظهور 800 أو 1300 ميكس لاحقًا.")
            return

        log_info(
            f"محاولة أخيرة واحدة لتحويل كامل الرصيد المتاح حاليًا ({child_balance:,} ميكس) من الفرد للأونر."
        )
        accepted = self.child.transfer_mix(
            self.owner.current_dial,
            child_balance,
            "Hekaya_XRP_MIX_Child_1",
        )
        if accepted:
            log_ok(
                f"اتصالات أكدت طلب تحويل {child_balance:,} ميكس للأونر؛ لن أنتظر رجوع رصيد 800/1300."
            )
            return

        # A timeout/unclear response must be checked before any possible retry; never resend blindly.
        verification = self.child.get_status_and_child(timeout=(2, 4))
        if verification[0] is not None and int(verification[1] or 0) < child_balance:
            log_ok(
                f"تغير رصيد الفرد من {child_balance:,} إلى {max(0, int(verification[1] or 0)):,} بعد طلب التحويل؛ لن أعيد إرساله."
            )
        else:
            log_warn("رد تحويل الإيقاف غير مؤكد؛ لم أعد إرسال التحويل لتجنب تكراره.")

    def _run(self):
        # ---- فحص نظام الأونر ----
        log_step("فحص النظام الحالي قبل أي عملية...")
        initial = getattr(self, "initial_status", None)
        if initial is None:
            initial = self._read_initial_status()
        if initial is None or self.stopped or self.stop_requested:
            return
        plan_id, initial_mix, _ = initial
        if not plan_id:
            log_err("تعذر قراءة النظام الحالي؛ لن يبدأ التجديد أو التحويل")
            return

        current_name, _ = PLAN_IDS.get(plan_id, (plan_id, ""))
        next_plan = (
            "Hekaya_XRP_MIX_Parent_6"
            if "Parent_7" in plan_id
            else "Hekaya_XRP_MIX_Parent_7"
        )
        next_name, _ = PLAN_IDS.get(next_plan, (next_plan, ""))
        log_info(f"النظام عند البداية: {current_name} ({plan_id})")
        log_info(f"الرصيد عند البداية: {initial_mix:,} ميكس")
        log_info(f"النظام التالي حسب الحالة الحالية: {next_name}")
        self._set_live_state(
            phase="preparing",
            status="تجهيز الدورة بعد قراءة النظام الحالي",
            current_plan_id=plan_id,
            target_mix=self.target_mix,
            current_mix=initial_mix,
        )

        # ---- ترقية النظام ----
        if plan_id not in PLAN_IDS:
            log_err(f"النظام الحالي غير معروف ({plan_id})؛ أوقفت التشغيل قبل العمليات")
            return
        if "Parent_7" not in plan_id:
            if self.stop_requested:
                return
            target_plan = "Hekaya_XRP_MIX_Parent_7"
            self._set_live_state(
                phase="plan_change_request",
                status="إرسال طلب الترقية إلى 270",
                current_plan_id=plan_id,
                target_plan_id=target_plan,
            )
            log_info(f"النظام الحالي: {plan_id} → جارٍ الترقية إلى 270")
            accepted = self.owner.change_plan(target_plan)
            if accepted:
                log_info("اتصالات قبلت طلب الترقية؛ أنتظر تأكيد ظهور النظام المطلوب.")
            else:
                log_warn("رد طلب الترقية غير مؤكد؛ سأتحقق من النظام، وأعيد الطلب فقط بعد المهلة إذا أكدت قراءة ناجحة بقاء الخطة القديمة")
            if not self._wait_for_plan(target_plan, previous_plan_id=plan_id, target_pid=""):
                return

        if self.stopped or self.stop_requested:
            return

        # ---- التحقق من الفرد ----
        child_num = self._read_child_number()
        if self.stopped or self.stop_requested:
            return
        if not child_num:
            log_err("لم يظهر فرد مضاف في ثلاث قراءات ناجحة؛ لم أبدأ أي تجديد أو تحويل")
            return
        self.child_num = child_num

        log_info(f"الهدف: {self.target_mix:,} ميكس")
        print()

        # ---- الحلقة الرئيسية ----
        while not self.stopped and not self.stop_requested:
            self.cycle_count += 1
            print(f"{C.CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C.RESET}")
            log(f"🔄 الدورة رقم {self.cycle_count}", C.BOLD + C.CYAN)

            status = self._read_status(self.owner, "حالة الدورة الحالية")
            if status is None or self.stopped or self.stop_requested:
                return
            plan_id, mix_amount, child_num = status
            self._set_live_state(
                phase="cycle_status",
                status=f"فحص الدورة رقم {self.cycle_count}",
                current_plan_id=plan_id,
                current_mix=mix_amount,
                target_mix=self.target_mix,
                cycle_count=self.cycle_count,
            )
            if child_num:
                self.child_num = child_num
            else:
                # Keep using the member already confirmed; a partial status response is not proof of removal.
                child_num = getattr(self, "child_num", None)
            if plan_id not in PLAN_IDS:
                log_err(f"معرّف النظام غير معروف ({plan_id})؛ أوقفت الدورة قبل التجديد أو التحويل")
                return

            name, price = PLAN_IDS.get(plan_id, ("حكاية ميكسات 270", "270"))
            log(f"  النظام: {name}", C.GRAY)
            log(f"  الميكس الحالي: {mix_amount:,}", C.GRAY)
            log(f"  الهدف: {self.target_mix:,}", C.GRAY)
            progress = min(100, (mix_amount / self.target_mix * 100) if self.target_mix else 0)
            bar_len = 30
            filled = int(bar_len * progress / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            log(f"  [{bar}] {progress:.1f}%", C.YELLOW)

            if not child_num:
                log_err("لا يوجد فرد مضاف")
                return

            if mix_amount >= self.target_mix:
                print()
                log_ok("=" * 50)
                log_ok(f"🎉 تم الوصول للهدف!")
                log_ok(f"   الميكس المحقق: {mix_amount:,}")
                log_ok(f"   عدد الدورات: {self.cycle_count}")
                log_ok("=" * 50)
                self.outcome = "success"
                return

            # ---- تجديد الباقة ----
            if self.stop_requested:
                return
            self._set_live_state(
                phase="renewing",
                status=f"تجديد الباقة {name}",
                current_plan_id=plan_id,
                current_mix=mix_amount,
                cycle_count=self.cycle_count,
            )
            log_info(f"تجديد الباقة ({name})...")
            if not self.owner.renew_bundle_action(plan_id, price):
                log_warn("فشل التجديد")
            if not self._sleep(3):
                return

            # ---- تحويل الميكس للفرد ----
            status = self._read_status(self.owner, "رصيد الميكس بعد التجديد")
            if status is None or self.stopped or self.stop_requested:
                return
            _, new_mix, _ = status
            if new_mix > 0:
                if self.stop_requested:
                    return
                self._set_live_state(
                    phase="transfer_to_child",
                    status=f"تحويل {new_mix:,} ميكس للفرد",
                    current_plan_id=plan_id,
                    current_mix=mix_amount,
                    cycle_count=self.cycle_count,
                )
                log_info(f"تحويل {new_mix:,} ميكس للفرد {child_num}...")
                if not self.owner.transfer_mix(child_num, new_mix, plan_id):
                    log_warn("فشل التحويل، انتظار 10 ثواني...")
                    if not self._sleep(10):
                        return
                    continue
                log_ok("تم التحويل")
            else:
                self._set_live_state(
                    phase="waiting_for_mix",
                    status="لا يوجد ميكس جديد؛ أنتظر تحديث الرصيد",
                    current_plan_id=plan_id,
                    current_mix=mix_amount,
                    cycle_count=self.cycle_count,
                )
                log_warn("لا يوجد ميكس جديد، انتظار...")
                if not self._sleep(5):
                    return
                status = self._read_status(self.child, "رصيد الفرد")
                if status is None or self.stopped or self.stop_requested:
                    return
                _, child_mix, _ = status
                if child_mix <= 0:
                    if not self._sleep(10):
                        return
                    continue

            if not self._sleep(2):
                return

            # ---- تغيير النظام ----
            if plan_id == "Hekaya_XRP_MIX_Parent_7":
                target_plan = "Hekaya_XRP_MIX_Parent_6"
                target_pid = "951"
            elif plan_id == "Hekaya_XRP_MIX_Parent_6":
                target_plan = "Hekaya_XRP_MIX_Parent_7"
                target_pid = ""
            else:
                log_err(f"النظام غير مدعوم للتبديل ({plan_id})؛ أوقفت التشغيل")
                return

            if self.stop_requested:
                return
            self._set_live_state(
                phase="plan_change_request",
                status="إرسال طلب تغيير النظام",
                current_plan_id=plan_id,
                target_plan_id=target_plan,
                target_mix=self.target_mix,
                retries_done=0,
                retry_limit=self._retry_settings()[1],
                retry_interval_seconds=self._retry_settings()[0],
            )
            log_info(f"تغيير النظام إلى {target_plan}...")
            accepted = self.owner.change_plan(target_plan, target_pid)
            if accepted:
                log_info("اتصالات قبلت طلب تغيير النظام؛ أنتظر التأكيد قبل أي خطوة أخرى.")
            else:
                log_warn("رد تغيير النظام غير مؤكد؛ سأتحقق من الحالة وأعيد الطلب فقط بعد المهلة وقراءة مؤكدة للخطة القديمة")

            # لا نكمل النقل أو الدورة التالية إلا بعد ظهور المعرّف المطلوب بالضبط.
            log_info(f"فحص سريع لتأكيد الوصول إلى {target_plan}...")
            if not self._wait_for_plan(
                target_plan, previous_plan_id=plan_id, target_pid=target_pid
            ):
                return

            # ---- ترحيل ميكسات الفرد ----
            self._set_live_state(
                phase="child_balance_transfer",
                status="قراءة رصيد الفرد وترحيله للأونر",
                current_plan_id=target_plan,
                cycle_count=self.cycle_count,
            )
            log_info("ترحيل ميكسات الفرد للأونر...")
            status = self._read_status(self.child, "رصيد الفرد بعد تغيير النظام")
            if status is None or self.stopped or self.stop_requested:
                return
            _, child_mix, _ = status
            if child_mix > 0:
                if self.stop_requested:
                    return
                transfer_accepted = self.child.transfer_mix(
                    self.owner.current_dial,
                    child_mix,
                    "Hekaya_XRP_MIX_Child_1",
                )
                if self.stop_requested:
                    self._stop_child_transfer_attempted = True
                    self._stop_child_transfer_accepted = transfer_accepted
                    self._stop_child_transfer_amount = child_mix
                    return
                if transfer_accepted:
                    log_ok(f"تم ترحيل {child_mix:,} ميكس")
                else:
                    log_warn("فشل الترحيل")

            # ---- إعادة تسجيل الأونر ----
            log_info("إعادة تسجيل دخول الأونر...")
            # Re-authenticate with the original owner credentials after plan migration.
            new_owner_api = EtisalatAPI()
            if new_owner_api.login(self.owner_email, self.owner_pass):
                self.owner = new_owner_api
                log_ok("تم إعادة التسجيل")

            log_info("انتظار 20 ثانية قبل الدورة التالية...")
            if not self._sleep(20):
                return


# ============================================================
# 5) تفاعل التيرمنال
# ============================================================

def ask(prompt_text, secret=False):
    """يقرأ إدخال من المستخدم"""
    if secret:
        import getpass
        return getpass.getpass(f"{C.YELLOW}{prompt_text}{C.RESET} ")
    return input(f"{C.YELLOW}{prompt_text}{C.RESET} ").strip()


def login_flow(role_label, role_key):
    """تسجيل دخول كامل"""
    log_step(f"تسجيل دخول {role_label}")
    email = ask(f"📧 إيميل {role_label}:")
    if "@" not in email:
        log_err("إيميل غير صحيح")
        return None, None, None
    password = ask(f"🔑 باسورد {role_label}:", secret=True)

    log_info("جارٍ تسجيل الدخول...")
    api = EtisalatAPI()
    if not api.login(email, password):
        log_err(f"فشل تسجيل دخول {role_label}")
        return None, None, None

    log_ok(f"تم تسجيل دخول {role_label} — Dial: {api.current_dial}")
    return api, email, password


def main():
    banner()

    # ---- 1) تسجيل دخول الأونر ----
    owner_api, owner_email, owner_pass = login_flow("الأونر", "owner")
    if not owner_api:
        sys.exit(1)

    # ---- 2) تسجيل دخول الفرد ----
    print()
    child_api, child_email, child_pass = login_flow("الفرد", "child")
    if not child_api:
        sys.exit(1)

    # ---- 3) قراءة الحالة ----
    print()
    log_step("قراءة الحالة الحالية...")
    status = read_status_until_available(owner_api, lambda: False, log_info, "الحالة الحالية")
    if status is None:
        return
    plan_id, mix_amount, child_num = status
    if plan_id:
        name, _ = PLAN_IDS.get(plan_id, (plan_id, ""))
        log_info(f"النظام الحالي: {name}")
        log_info(f"الميكس الحالي: {mix_amount:,}")
        if child_num:
            log_info(f"الفرد المرتبط: {child_num}")
        else:
            log_warn("لا يوجد فرد مرتبط — لازم تضيف فرد أولاً")
    else:
        log_warn("فشل قراءة الحالة")

    # ---- 4) اختيار الهدف ----
    print()
    log_step("اختر الهدف")
    print(f"  {C.CYAN}1){C.RESET} 50,000 ميكس")
    print(f"  {C.CYAN}2){C.RESET} 100,000 ميكس")
    print(f"  {C.CYAN}3){C.RESET} 150,000 ميكس")
    print(f"  {C.CYAN}4){C.RESET} 200,000 ميكس")
    print(f"  {C.CYAN}5){C.RESET} إدخال يدوي")

    choice = ask("اختيارك [1-5]:")

    targets = {1: 50000, 2: 100000, 3: 150000, 4: 200000}
    if choice in ("1", "2", "3", "4"):
        target = targets[int(choice)]
    elif choice == "5":
        try:
            target = int(ask("أدخل الهدف:"))
            if target <= 0:
                raise ValueError
        except ValueError:
            log_err("رقم غير صحيح")
            sys.exit(1)
    else:
        log_err("اختيار غير صحيح")
        sys.exit(1)

    # ---- 5) تأكيد ----
    print()
    log_info(f"الهدف: {target:,} ميكس")
    log_info(f"عدد الدورات المتوقع: ~{target // 5000 if target else 0}")
    confirm = ask("ابدأ الدورة؟ [y/n]:")
    if confirm.lower() not in ("y", "yes", "نعم"):
        log_warn("تم الإلغاء")
        return

    # ---- 6) تشغيل الدورة ----
    print()
    loop = AutoLoop(owner_api, child_api, target)
    loop.owner_email = owner_email
    loop.owner_pass = owner_pass
    loop._run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        log_warn("تم الإيقاف بواسطة المستخدم")
        sys.exit(0)