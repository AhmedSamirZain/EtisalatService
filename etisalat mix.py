
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

# Operation-specific signatures and request headers ported from the supplied legacy client.
# These are protocol signatures, not user credentials; TLS verification remains enabled.
LEGACY_SIGNATURES = {
    "migrate_270": {
        "headerSignature": "db5a889033c9e5d767693d0967d621bdc2e5488d3c3153db63ceb706a9bacca88763fbaf2fa7e20202f4ee18a707409f4612d6e05d2beb189f0793ec5c9c7663",
        "urlSignature": "80c5c15a6bdc1c45be7b875cb49531329d32368a1cbb04963cd6f71cbf9ab563ca1bf10c67158720e879aee85b8d7789c88418d21827ec1499f13b536bbc59ca",
        "bodySignature": "ae06965020a0e291ce672e1ec6b0a5e7f64fb423074d21f93ffd1bdd005943f57cf4c794d96c8c7cfa75461677deb4d9542936787007eabdc0c287ab005d4cff",
    },
    "migrate_180": {
        "headerSignature": "e5de43ce373d2029adc0c377232e414723618fe18ed19e2e1a7df22f68dcf24e26dc028eaa8ce6e47f567916f7e4f56607e81676a213f45f62547c46680d9608",
        "urlSignature": "80c5c15a6bdc1c45be7b875cb49531329d32368a1cbb04963cd6f71cbf9ab563ca1bf10c67158720e879aee85b8d7789c88418d21827ec1499f13b536bbc59ca",
        "bodySignature": "bebb62f931fffdb73e84662d3ce99c6805332bc59a1eb222bc304f918468e1382538b072944666304a9d0d85c20d26bbecf870888115b9afcf6bdc6bb27c8ac4",
    },
    "inquiry_owner": {
        "headerSignature": "f68f4c6bdd00242642bcd0c0718f2faf4d8d15fd0391ed3438f45986f1de2e63976362a540b474f779f57f5bf653654fb660f0e5f0e581c56b6cbe44a6c92174",
        "urlSignature": "61dce994a58f4f16bbe85126332b344e6e1c43eb59fe6aee7bf53758a568ede4f80a8bf4864e786ff481e03d544116e8bd4f92d36a4a1a2da31d3bee23ab3fc4",
    },
    "inquiry_child": {
        "headerSignature": "ba8c1d3a3515a0d082d1e6ca749910d922f0b47cd7a02fa0c901fb1731ea843959a5195929bccdf943aea0e73f721c5f72d69d5f7362107fc64f5d61ef3a56d1",
        "urlSignature": "25b03790bd60b52841a34e39d87b9ada63f0a1b1ee700ddc63b030b3ec2fd23115e8c709cd6410ec1e83bd29d457a22f917954fedb6580fae52320434a5a02d6",
    },
    "transfer_owner": {
        "headerSignature": "f68f4c6bdd00242642bcd0c0718f2faf4d8d15fd0391ed3438f45986f1de2e63976362a540b474f779f57f5bf653654fb660f0e5f0e581c56b6cbe44a6c92174",
        "urlSignature": "dd15f4e5e1cb6178cd6edc9842f406992e73bddcc78a5c7d58f90a1a73d0bab5315d5734e8b9465575fde1c7be0c5926ac95672df50f12493b5c4586286264bd",
        "bodySignature": "fcf9bc689207d798aa02e249f27163186c130729c761c28b20b046f39ac906a8c9ab9f56d32d22d6bd84a1d0bb30b3056f38119ef6ce2419f07d3432edc7e6fb",
    },
    "transfer_child": {
        "headerSignature": "ba8c1d3a3515a0d082d1e6ca749910d922f0b47cd7a02fa0c901fb1731ea843959a5195929bccdf943aea0e73f721c5f72d69d5f7362107fc64f5d61ef3a56d1",
        "urlSignature": "dd15f4e5e1cb6178cd6edc9842f406992e73bddcc78a5c7d58f90a1a73d0bab5315d5734e8b9465575fde1c7be0c5926ac95672df50f12493b5c4586286264bd",
        "bodySignature": "62281bf02fffdd4cfbd814e0b3f574f744111ad4974756476be49462b8a2bc6a1b484d3bc1cb8e7d9847cf6c273dc18ac484c606b686252347ed66525230cb25",
    },
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
            pause, timeout, mode = 0.25, (3, 10), "سريع"
        elif attempt < 12:
            pause, timeout, mode = 0.75, (5, 30), "متوسط"
        else:
            # The working legacy client allowed slow Etisalat responses up to 60 seconds.
            pause, timeout, mode = 2.0, (5, 60), "مستمر"

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
        self.account_role = "owner"
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

    def _legacy_operation_headers(self, signature, content_type):
        headers = self.base_headers.copy()
        headers.update({
            "Accept": "text/xml",
            "OS-Version": "12",
            "C-Type": "4G",
            "Host": "mab.etisalat.com.eg:11003",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "User-Agent": "okhttp/5.0.0-alpha.11",
            "Content-Type": content_type,
            "headerSignature": signature["headerSignature"],
            "urlSignature": signature["urlSignature"],
        })
        if "bodySignature" in signature:
            headers["bodySignature"] = signature["bodySignature"]
        return headers

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
        if plan_name == "Hekaya_XRP_MIX_Parent_6" and str(plan_id) == "951":
            signature = LEGACY_SIGNATURES["migrate_180"]
        elif plan_name == "Hekaya_XRP_MIX_Parent_7" and str(plan_id) == "":
            signature = LEGACY_SIGNATURES["migrate_270"]
        else:
            log_warn(f"رفض طلب تغيير غير مدعوم: {plan_name} / ratePlanID={plan_id}")
            return False
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
        headers = self._legacy_operation_headers(
            signature, "text/xml; charset=UTF-8"
        )
        try:
            response = self.session.post(
                BASE_URL + url_path,
                headers=headers,
                data=xml_body.encode("utf-8"),
                timeout=(5, 60),
            )
            return response.status_code == 200 and "<status>true</status>" in response.text
        except Exception as exc:
            # Do not resend this paid request here; AutoLoop checks the exact plan ID first.
            log_warn(f"change_plan: {exc}")
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

    def get_transferable_mix(self, product_name):
        """Query legacy transferInquiry; None means the response was not reliable."""
        if not self.current_dial:
            return None
        role = "child" if getattr(self, "account_role", "owner") == "child" else "owner"
        signature = LEGACY_SIGNATURES[f"inquiry_{role}"]
        xml_body = (
            "<transferInquiryRequest>"
            f"<productId>{product_name}</productId>"
            f"<subscriberNumber>{self.current_dial}</subscriberNumber>"
            "<language>1</language>"
            "</transferInquiryRequest>"
        )
        url = BASE_URL + "/Saytar/rest/emerald/transferInquiry"
        headers = self._legacy_operation_headers(signature, "text/xml")
        try:
            response = self.session.get(
                url,
                headers=headers,
                params={"req": xml_body},
                timeout=(5, 60),
            )
            if response.status_code != 200 or "<status>true</status>" not in response.text:
                return None
            root = ET.fromstring(response.text)
            for assigned in root.findall(".//assigned"):
                if assigned.findtext("dial") != self.current_dial:
                    continue
                value = assigned.findtext("value")
                try:
                    return max(0, int(float(value or 0)))
                except (TypeError, ValueError):
                    return None
            return None
        except Exception as exc:
            log_warn(f"transfer inquiry: {exc}")
            return None

    def transfer_mix(self, receiver_number, quota_amount, product_name):
        try:
            quota = int(quota_amount)
        except (TypeError, ValueError):
            return False
        if quota <= 0 or not self.current_dial or not receiver_number:
            return False
        role = "child" if getattr(self, "account_role", "owner") == "child" else "owner"
        signature = LEGACY_SIGNATURES[f"transfer_{role}"]
        xml_body = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
            "<newFamilyTransferRequest>"
            "<distribute>false</distribute>"
            f"<msisdn>{self.current_dial}</msisdn>"
            f"<productName>{product_name}</productName>"
            f"<quota>{quota}</quota>"
            f"<receiverMsisdn>{receiver_number}</receiverMsisdn>"
            f"<senderMsisdn>{self.current_dial}</senderMsisdn>"
            "<type>UNITS</type>"
            "</newFamilyTransferRequest>"
        )
        url = BASE_URL + "/Saytar/rest/services/newTransfer/v2"
        headers = self._legacy_operation_headers(
            signature, "text/xml; charset=UTF-8"
        )
        try:
            response = self.session.post(
                url,
                headers=headers,
                data=xml_body.encode("utf-8"),
                timeout=(5, 60),
            )
            if response.status_code == 200 and "<status>true</status>" in response.text:
                return True
            if "<status>false</status>" in response.text:
                return False
            # Do not infer rejection from a timeout, HTTP error, or unexpected response body.
            return None
        except Exception as exc:
            log_warn(f"transfer: {exc}")
            return None


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
        for account, role in ((self.owner, "owner"), (self.child, "child")):
            try:
                account.account_role = role
            except Exception:
                pass
        self.target_mix = target_mix
        self.cycle_count = 0
        self.stopped = False
        self.stop_requested = False
        self.outcome = "failed"
        self._stop_child_transfer_attempted = False
        self._stop_child_transfer_accepted = False
        self._stop_child_transfer_amount = 0
        self._transfer_backoff_seconds = None
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

    def _runtime_settings(self):
        try:
            settings = self._retry_settings_provider() if self._retry_settings_provider else {}
        except Exception:
            settings = {}
        return settings if isinstance(settings, dict) else {}

    def _retry_settings(self):
        settings = self._runtime_settings()
        interval = max(60, int(settings.get("plan_retry_seconds", 240)))
        retry_limit = max(0, int(settings.get("plan_retry_limit", 3)))
        return interval, retry_limit

    def _transfer_retry_settings(self):
        settings = self._runtime_settings()
        interval = max(1, int(settings.get("transfer_retry_seconds", 3)))
        retry_limit = max(0, int(settings.get("transfer_retry_limit", 3)))
        return interval, retry_limit

    def _current_transfer_backoff(self):
        base_interval, _ = self._transfer_retry_settings()
        current = self._transfer_backoff_seconds
        if current is None:
            current = base_interval
        else:
            current = max(base_interval, int(current))
        self._transfer_backoff_seconds = min(60, current)
        return self._transfer_backoff_seconds

    def _increase_transfer_backoff(self):
        self._transfer_backoff_seconds = min(60, self._current_transfer_backoff() + 1)
        return self._transfer_backoff_seconds

    def _decrease_transfer_backoff_after_success(self):
        base_interval, _ = self._transfer_retry_settings()
        self._transfer_backoff_seconds = max(
            base_interval, self._current_transfer_backoff() - 1
        )
        return self._transfer_backoff_seconds

    def _migration_settle_seconds(self):
        try:
            return max(0, int(self._runtime_settings().get("migration_settle_seconds", 60)))
        except (TypeError, ValueError):
            return 60

    def _post_transfer_wait_seconds(self):
        try:
            return max(0, int(self._runtime_settings().get("post_transfer_wait_seconds", 3)))
        except (TypeError, ValueError):
            return 3

    def _transfer_available_product(self, sender, receiver_number, product_id, product_name, label):
        """Transfer only an inquiry-confirmed amount, verifying ambiguous outcomes before retry."""
        interval, retry_limit = self._transfer_retry_settings()
        attempts = 0
        pending_amount = None
        pending_ambiguous = False
        unchanged_checks = 0
        polls = 0
        limit_notice_sent = False
        def wait_before_next_check(reason):
            delay = self._current_transfer_backoff()
            self._set_live_state(
                transfer_retry_wait_seconds=delay,
                status=f"{reason}؛ إعادة الفحص بعد {delay} ثانية",
            )
            log_info(f"{reason}؛ انتظار {delay} ثانية قبل إعادة الفحص أو المحاولة التالية.")
            if not self._sleep(delay):
                return False
            next_delay = self._increase_transfer_backoff()
            self._set_live_state(transfer_next_retry_seconds=next_delay)
            return True

        while not self.stopped and not self.stop_requested:
            available = sender.get_transferable_mix(product_id)
            if self.stopped or self.stop_requested:
                return False
            polls += 1
            interval, retry_limit = self._transfer_retry_settings()
            limit_label = "بلا حد" if retry_limit == 0 else str(retry_limit + 1)
            self._set_live_state(
                phase="transfer_inquiry",
                status=f"استعلام الكمية القابلة للتحويل: {label}",
                transfer_attempts=attempts,
                transfer_retry_limit=retry_limit,
                transfer_retry_seconds=interval,
                transfer_retry_wait_seconds=None,
                transfer_next_retry_seconds=self._current_transfer_backoff(),
                transfer_amount=available,
            )
            if available is None:
                if pending_ambiguous:
                    unchanged_checks = 0
                if polls == 1 or polls % 5 == 0:
                    log_warn(f"تعذر تأكيد الكمية القابلة للتحويل من {label}؛ لن أرسل طلب تحويل غير مؤكد.")
                if not wait_before_next_check("تعذر الاستعلام الموثوق"):
                    return False
                continue

            available = max(0, int(available))
            if pending_amount is not None:
                if available != pending_amount:
                    log_ok(
                        f"تغيرت الكمية القابلة للتحويل بعد الطلب من {pending_amount:,} إلى "
                        f"{available:,}؛ لن أعيد الكمية القديمة، وسأعتمد القراءة الحالية فقط."
                    )
                    pending_amount = None
                    pending_ambiguous = False
                    unchanged_checks = 0
                elif pending_ambiguous:
                    unchanged_checks += 1
                    if unchanged_checks < 2:
                        log_warn(
                            f"نتيجة التحويل السابق ملتبسة والكمية ما زالت {available:,}؛ "
                            "أتحقق مرة أخرى قبل السماح بأي إعادة."
                        )
                        if not wait_before_next_check("الكمية بقيت نفسها بعد نتيجة ملتبسة"):
                            return False
                        continue
                    log_warn(
                        f"استعلامان متتاليان أكدا بقاء {available:,} ميكس بعد النتيجة الملتبسة؛ "
                        "سأسمح بمحاولة محدودة جديدة."
                    )
                    pending_amount = None
                    pending_ambiguous = False
                    unchanged_checks = 0
                else:
                    # A literal status=false is a confirmed rejection; a fresh inquiry still gates retry.
                    pending_amount = None

            if available <= 0:
                log_info(f"استعلام التحويل أكد عدم وجود كمية متاحة في {label}؛ أتجاوز هذه الخطوة.")
                return True

            if retry_limit and attempts > retry_limit:
                self._set_live_state(
                    phase="transfer_retry_limit",
                    status="بلغت محاولات التحويل حد الإعداد؛ أراقب الكمية دون طلبات جديدة",
                    transfer_attempts=attempts,
                    transfer_retry_limit=retry_limit,
                    transfer_retry_seconds=interval,
                )
                if not limit_notice_sent:
                    log_warn(
                        f"بلغت حد محاولات التحويل ({limit_label})؛ سأواصل استعلام الكمية "
                        "دون إرسال طلبات إضافية حتى تتغير القراءة أو يوقف المدير التشغيل."
                    )
                    limit_notice_sent = True
                if not wait_before_next_check("بلغ حد إعادات التحويل؛ أواصل المراقبة دون إرسال"):
                    return False
                continue

            if self.stop_requested:
                return False
            self._set_live_state(
                phase="transfer_sending",
                status=f"تحويل {available:,} ميكس: {label}",
                transfer_amount=available,
                transfer_attempts=attempts,
                transfer_retry_limit=retry_limit,
                transfer_retry_seconds=interval,
            )
            log_info(
                f"استعلام التحويل أكد {available:,} ميكس قابلة للتحويل في {label}؛ "
                f"إرسال المحاولة {attempts + 1}/{limit_label}."
            )
            result = sender.transfer_mix(receiver_number, available, product_name)
            attempts += 1
            if self.stop_requested:
                if sender is self.child:
                    self._stop_child_transfer_attempted = True
                    self._stop_child_transfer_accepted = result is True
                    self._stop_child_transfer_amount = available
                return False
            if result is True:
                log_ok(f"قُبل تحويل {available:,} ميكس في {label}.")
                self._set_live_state(
                    phase="transfer_confirmed",
                    status=f"قُبل تحويل {available:,} ميكس: {label}",
                    transfer_attempts=attempts,
                )
                next_delay = self._decrease_transfer_backoff_after_success()
                self._set_live_state(
                    transfer_retry_wait_seconds=None,
                    transfer_next_retry_seconds=next_delay,
                )
                log_info(
                    f"نجح التحويل؛ خفّضت مهلة إعادة المحاولة للعملية التالية إلى {next_delay} ثانية."
                )
                if sender is self.child:
                    self._stop_child_transfer_attempted = True
                    self._stop_child_transfer_accepted = True
                    self._stop_child_transfer_amount = available
                post_wait = self._post_transfer_wait_seconds()
                if post_wait and not self._sleep(post_wait):
                    return False
                if sender is self.child:
                    self._stop_child_transfer_attempted = False
                    self._stop_child_transfer_accepted = False
                    self._stop_child_transfer_amount = 0
                return True

            pending_amount = available
            pending_ambiguous = result is None
            unchanged_checks = 0
            limit_notice_sent = False
            if result is None:
                log_warn("استجابة التحويل ملتبسة؛ لن أعيد الطلب قبل استعلام جديد، وتأكيد بقاء الكمية في قراءتين.")
            else:
                log_warn("اتصالات رفضت طلب التحويل؛ سأعيد الاستعلام قبل أي محاولة محدودة أخرى.")
            if not wait_before_next_check(
                "نتيجة التحويل غير مؤكدة" if result is None else "اتصالات رفضت طلب التحويل"
            ):
                return False
        return False

    def _settle_after_plan_change(self, plan_id):
        seconds = self._migration_settle_seconds()
        if seconds <= 0:
            return not self.stop_requested
        self._set_live_state(
            phase="plan_settling",
            status=f"انتظار استقرار النظام {plan_id} لمدة {seconds} ثانية",
            current_plan_id=plan_id,
            migration_settle_seconds=seconds,
        )
        log_info(f"تم تأكيد النظام {plan_id}؛ انتظار استقرار {seconds} ثانية قبل التحويل التالي.")
        return self._sleep(seconds)

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
        """Run the approved legacy sequence with exact plan checks and inquiry-gated transfers."""
        log_step("فحص نظام الأونر قبل بدء الدورة...")
        initial = getattr(self, "initial_status", None)
        if initial is None:
            initial = self._read_initial_status()
        if initial is None or self.stopped or self.stop_requested:
            return
        plan_id, initial_mix, _ = initial
        parent_270 = "Hekaya_XRP_MIX_Parent_7"
        parent_180 = "Hekaya_XRP_MIX_Parent_6"
        child_product = "Hekaya_XRP_MIX_Child_1"
        if not plan_id:
            log_err("تعذر قراءة النظام الحالي؛ لن يبدأ أي تحويل أو تغيير باقة")
            return
        if plan_id != parent_270:
            self._set_live_state(
                phase="unexpected_start_plan",
                status="الدورة القديمة تبدأ من 270؛ أوقفت التشغيل دون تغيير تلقائي",
                current_plan_id=plan_id,
                target_mix=self.target_mix,
            )
            log_err(
                f"النظام عند البدء هو {plan_id} وليس {parent_270} (270). "
                "لن أخمّن موضع الدورة أو أغيّر الباقة تلقائيًا؛ راجع حالة الأونر أولًا."
            )
            return

        log_info(f"النظام عند البداية: حكاية ميكسات 270 ({plan_id})")
        log_info(f"الرصيد عند البداية: {initial_mix:,} ميكس")
        self._set_live_state(
            phase="preparing", status="تجهيز الدورة القديمة بدءًا من 270",
            current_plan_id=plan_id, target_mix=self.target_mix, current_mix=initial_mix,
        )
        child_num = self._read_child_number()
        if self.stopped or self.stop_requested:
            return
        if not child_num:
            log_err("لم يمكن تأكيد رقم الفرد؛ لم أبدأ أي تحويل أو تغيير باقة")
            return
        owner_dial = getattr(self.owner, "current_dial", None)
        child_dial = getattr(self.child, "current_dial", None)
        if not owner_dial or not child_dial:
            log_err("رقم الأونر أو الفرد غير متاح من جلسة الدخول؛ أوقفت التشغيل قبل أي تحويل.")
            return
        if str(child_dial) != str(child_num):
            log_err(f"رقم الفرد المرتبط بالأونر ({child_num}) لا يطابق الحساب المسجل ({child_dial}); أوقفت التشغيل.")
            return
        if str(child_num) == str(owner_dial):
            log_err("رقم الفرد يطابق رقم الأونر؛ أوقفت التشغيل قبل أي تحويل.")
            return
        self.child_num = child_num
        log_info(f"الهدف: {self.target_mix:,} ميكس")

        while not self.stopped and not self.stop_requested:
            status = self._read_status(self.owner, "رصيد الأونر قبل الدورة")
            if status is None or self.stopped or self.stop_requested:
                return
            current_plan, owner_mix, observed_child = status
            if observed_child:
                if str(observed_child) != str(child_num):
                    log_err(
                        f"تغير رقم الفرد المرتبط من {child_num} إلى {observed_child} أثناء التشغيل؛ "
                        "أوقفت الدورة قبل التحويل إلى رقم مختلف."
                    )
                    return
                self.child_num = child_num = observed_child
            if current_plan != parent_270:
                self._set_live_state(
                    phase="unexpected_cycle_plan",
                    status="النظام ليس 270 عند بداية الدورة؛ توقفت دون تغيير تلقائي",
                    current_plan_id=current_plan, target_mix=self.target_mix,
                )
                log_err(f"بداية الدورة تتطلب تأكيد {parent_270} لكن القراءة الحالية {current_plan}؛ توقفت بأمان.")
                return

            self.cycle_count += 1
            self._set_live_state(
                phase="cycle_status", status=f"بدء الدورة رقم {self.cycle_count} من النظام 270",
                current_plan_id=current_plan, current_mix=owner_mix,
                target_mix=self.target_mix, cycle_count=self.cycle_count,
            )
            log_step(f"الدورة رقم {self.cycle_count}: الأونر على 270")
            log_info(f"رصيد الأونر: {owner_mix:,} ميكس؛ الهدف: {self.target_mix:,}")
            if owner_mix >= self.target_mix:
                self.outcome = "success"
                self._set_live_state(
                    phase="success", status="تم بلوغ الهدف", current_plan_id=current_plan,
                    current_mix=owner_mix, target_mix=self.target_mix,
                )
                log_ok(f"تم بلوغ الهدف: {owner_mix:,} ميكس بعد {self.cycle_count - 1} دورة مكتملة.")
                return

            # 1) Owner's transferable 270 mix -> child.
            self._set_live_state(
                phase="transfer_owner_270_to_child",
                status="استعلام وتحويل الكمية المتاحة من الأونر 270 إلى الفرد",
                current_plan_id=parent_270, cycle_count=self.cycle_count,
            )
            if not self._transfer_available_product(
                self.owner, child_num, parent_270, parent_270, "الأونر على 270 إلى الفرد"
            ) or self.stop_requested:
                return

            # 2) Owner 270 -> 180; no next step before exact confirmation.
            self._set_live_state(
                phase="plan_change_request", status="إرسال طلب تغيير الأونر من 270 إلى 180",
                current_plan_id=parent_270, target_plan_id=parent_180,
                cycle_count=self.cycle_count,
            )
            log_info("الخطوة 2/6: تغيير الأونر من 270 إلى 180.")
            accepted = self.owner.change_plan(parent_180, "951")
            log_info("قُبل طلب التغيير إلى 180؛ أنتظر المعرّف بالضبط." if accepted else
                     "نتيجة طلب 180 غير مؤكدة؛ لن أعيد الطلب بسبب timeout أو قراءة غير مقروءة.")
            if not self._wait_for_plan(parent_180, previous_plan_id=parent_270, target_pid="951"):
                return
            if not self._settle_after_plan_change(parent_180):
                return

            # 3) Child's transferable balance -> owner while owner is on 180.
            self._set_live_state(
                phase="transfer_child_to_owner_on_180",
                status="استعلام وتحويل ميكسات الفرد إلى الأونر بعد تأكيد 180",
                current_plan_id=parent_180, cycle_count=self.cycle_count,
            )
            log_info("الخطوة 3/6: تحويل ميكسات الفرد إلى الأونر بعد تأكيد 180.")
            if not self._transfer_available_product(
                self.child, self.owner.current_dial, child_product, child_product,
                "الفرد إلى الأونر بعد تغيير 180",
            ) or self.stop_requested:
                return

            # 4) Owner's transferable 180 mix -> child.
            self._set_live_state(
                phase="transfer_owner_180_to_child",
                status="استعلام وتحويل الكمية المتاحة من الأونر 180 إلى الفرد",
                current_plan_id=parent_180, cycle_count=self.cycle_count,
            )
            log_info("الخطوة 4/6: تحويل ميكسات الأونر على 180 إلى الفرد.")
            if not self._transfer_available_product(
                self.owner, child_num, parent_180, parent_180, "الأونر على 180 إلى الفرد"
            ) or self.stop_requested:
                return

            # 5) Owner 180 -> 270; exact confirmation and settling precede step 6.
            self._set_live_state(
                phase="plan_change_request", status="إرسال طلب إعادة الأونر من 180 إلى 270",
                current_plan_id=parent_180, target_plan_id=parent_270,
                cycle_count=self.cycle_count,
            )
            log_info("الخطوة 5/6: إعادة باقة الأونر من 180 إلى 270.")
            accepted = self.owner.change_plan(parent_270, "")
            log_info("قُبل طلب التغيير إلى 270؛ أنتظر المعرّف بالضبط." if accepted else
                     "نتيجة طلب 270 غير مؤكدة؛ لن أعيد الطلب بسبب timeout أو قراءة غير مقروءة.")
            if not self._wait_for_plan(parent_270, previous_plan_id=parent_180, target_pid=""):
                return
            if not self._settle_after_plan_change(parent_270):
                return

            # 6) Child -> owner while on 270, then inspect owner balance vs target.
            self._set_live_state(
                phase="transfer_child_to_owner_on_270",
                status="استعلام وتحويل ميكسات الفرد إلى الأونر على 270",
                current_plan_id=parent_270, cycle_count=self.cycle_count,
            )
            log_info("الخطوة 6/6: تحويل ميكسات الفرد إلى الأونر على 270.")
            if not self._transfer_available_product(
                self.child, self.owner.current_dial, child_product, child_product,
                "الفرد إلى الأونر بعد تأكيد 270",
            ) or self.stop_requested:
                return

            self._set_live_state(
                phase="target_check", status="قراءة رصيد الأونر بعد اكتمال الخطوات الست",
                current_plan_id=parent_270, cycle_count=self.cycle_count,
            )
            final_status = self._read_status(self.owner, "رصيد الأونر بعد الدورة")
            if final_status is None or self.stopped or self.stop_requested:
                return
            final_plan, final_mix, _ = final_status
            if final_plan != parent_270:
                self._set_live_state(
                    phase="target_check_unexpected_plan",
                    status="تعذر تأكيد 270 بعد الدورة؛ توقفت دون بدء دورة أخرى",
                    current_plan_id=final_plan, target_plan_id=parent_270, current_mix=final_mix,
                )
                log_err(f"قراءة ما بعد الدورة أعادت النظام {final_plan} بدل {parent_270}؛ لن أبدأ دورة جديدة.")
                return
            self._set_live_state(
                phase="target_check", status="تم فحص رصيد الأونر بعد الدورة",
                current_plan_id=final_plan, current_mix=final_mix,
                target_mix=self.target_mix, cycle_count=self.cycle_count,
            )
            log_info(f"رصيد الأونر بعد الدورة: {final_mix:,} ميكس؛ الهدف: {self.target_mix:,}.")
            if final_mix >= self.target_mix:
                self.outcome = "success"
                self._set_live_state(
                    phase="success", status="تم بلوغ الهدف", current_plan_id=final_plan,
                    current_mix=final_mix, target_mix=self.target_mix,
                    cycle_count=self.cycle_count,
                )
                log_ok(f"تم بلوغ الهدف: {final_mix:,} ميكس بعد {self.cycle_count} دورة.")
                return
            log_info("لم يصل الأونر إلى الهدف؛ ستبدأ دورة جديدة بعد قراءة 270 المؤكدة.")
            if not self._sleep(1):
                return

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