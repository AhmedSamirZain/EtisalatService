
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
def log_step(msg):  log(f"\n▶ {msg}", C.BOLD + C.BLUE)


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
        except Exception as e:
            log_warn(f"get_status: {e}")
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
    def __init__(self, owner: EtisalatAPI, child: EtisalatAPI, target_mix: int):
        self.owner = owner
        self.child = child
        self.target_mix = target_mix
        self.cycle_count = 0
        self.stopped = False

    def stop(self):
        self.stopped = True

    def _sleep(self, seconds):
        """نوم قابل للإيقاف مع دعم فواصل فحص أقل من ثانية."""
        deadline = time.monotonic() + max(0.0, float(seconds))
        while not self.stopped:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(0.1, remaining))
        return False

    def _wait_for_plan(self, expected_plan_id):
        """Poll quickly, then allow a few longer server responses; require an exact match."""
        fast_attempts = 6
        slow_attempts = 2
        total = fast_attempts + slow_attempts
        for attempt in range(total):
            if self.stopped:
                return False
            fast = attempt < fast_attempts
            if not self._sleep(0.25 if fast else 1.0):
                return False
            timeout = (2, 3) if fast else (3, 10)
            current_plan, _, _ = self.owner.get_status_and_child(timeout=timeout)
            if current_plan == expected_plan_id:
                log_ok(f"تم تأكيد النظام المطلوب: {current_plan}")
                return True
            log_info(f"فحص النظام بعد التغيير: {attempt + 1}/{total}")
        log_err(f"لم يتم تأكيد النظام المطلوب {expected_plan_id}؛ لن أكمل إلى عملية أخرى")
        return False

    def run(self):
        try:
            self._run()
        except KeyboardInterrupt:
            log_warn("\nتم الإيقاف بواسطة المستخدم (Ctrl+C)")
        except Exception as e:
            log_err(f"خطأ غير متوقع: {e}")
            import traceback
            traceback.print_exc()

    def _run(self):
        # ---- فحص نظام الأونر ----
        log_step("فحص النظام الحالي قبل أي عملية...")
        initial = getattr(self, "initial_status", None)
        if initial is None:
            initial = self.owner.get_status_and_child(timeout=(3, 10))
        plan_id, initial_mix, _ = initial
        if self.stopped:
            return
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

        # ---- ترقية النظام ----
        if plan_id not in PLAN_IDS:
            log_err(f"النظام الحالي غير معروف ({plan_id})؛ أوقفت التشغيل قبل العمليات")
            return
        if "Parent_7" not in plan_id:
            target_plan = "Hekaya_XRP_MIX_Parent_7"
            log_info(f"النظام الحالي: {plan_id} → جارٍ الترقية إلى 270")
            if not self.owner.change_plan(target_plan):
                log_err("طلب الترقية إلى 270 لم يُقبل؛ أوقفت التشغيل")
                return
            if not self._wait_for_plan(target_plan):
                return

        if self.stopped:
            return

        # ---- التحقق من الفرد ----
        _, _, child_num = self.owner.get_status_and_child(timeout=(2, 3))
        if not child_num:
            log_err("لا يوجد فرد مضاف — أضف فرداً أولاً")
            return

        log_ok(f"الفرد: {child_num}")
        log_info(f"الهدف: {self.target_mix:,} ميكس")
        print()

        # ---- الحلقة الرئيسية ----
        while not self.stopped:
            self.cycle_count += 1
            print(f"{C.CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C.RESET}")
            log(f"🔄 الدورة رقم {self.cycle_count}", C.BOLD + C.CYAN)

            plan_id, mix_amount, child_num = self.owner.get_status_and_child(timeout=(2, 3))
            if self.stopped:
                return
            if not plan_id:
                log_warn("فشل قراءة النظام، انتظار 5 ثواني...")
                if not self._sleep(5):
                    return
                continue

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
                return

            # ---- تجديد الباقة ----
            log_info(f"تجديد الباقة ({name})...")
            if not self.owner.renew_bundle_action(plan_id, price):
                log_warn("فشل التجديد")
            if not self._sleep(3):
                return

            # ---- تحويل الميكس للفرد ----
            _, new_mix, _ = self.owner.get_status_and_child(timeout=(2, 3))
            if self.stopped:
                return
            if new_mix > 0:
                log_info(f"تحويل {new_mix:,} ميكس للفرد {child_num}...")
                if not self.owner.transfer_mix(child_num, new_mix, plan_id):
                    log_warn("فشل التحويل، انتظار 10 ثواني...")
                    if not self._sleep(10):
                        return
                    continue
                log_ok("تم التحويل")
            else:
                log_warn("لا يوجد ميكس جديد، انتظار...")
                if not self._sleep(5):
                    return
                _, child_mix, _ = self.child.get_status_and_child(timeout=(2, 3))
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

            log_info(f"تغيير النظام إلى {target_plan}...")
            if not self.owner.change_plan(target_plan, target_pid):
                log_err("طلب تغيير النظام لم يُقبل؛ أوقفت التشغيل بدل بدء دورة أخرى")
                return

            # لا نكمل النقل أو الدورة التالية إلا بعد ظهور المعرّف المطلوب بالضبط.
            log_info(f"فحص سريع لتأكيد الوصول إلى {target_plan}...")
            if not self._wait_for_plan(target_plan):
                return

            # ---- ترحيل ميكسات الفرد ----
            log_info("ترحيل ميكسات الفرد للأونر...")
            _, child_mix, _ = self.child.get_status_and_child(timeout=(2, 3))
            if child_mix > 0:
                if self.child.transfer_mix(self.owner.current_dial, child_mix,
                                           "Hekaya_XRP_MIX_Child_1"):
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
    plan_id, mix_amount, child_num = owner_api.get_status_and_child()
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