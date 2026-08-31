"""Real Meesho API client — OTPLESS login flow + FOD (first-order discount).

Flow (reverse-engineered from Fiddler captures + decoded APK):
  1. GET  https://user-auth.otpless.app/v2/state?...
     -> {"state": "<uuid>"}
  2. POST https://user-auth.otpless.app/v3/lp/user/transaction/intent/{state}
     -> quantumLeap { uid, channelAuthToken, asId }
     (OTP is delivered via WhatsApp/SMS by OTPLESS)
  3. POST https://user-auth.otpless.app/v3/lp/user/transaction/otp/{state}
     -> oneTap.token + oneTap.merchantUserInfo.idToken (RS256 JWT from OTPLESS)
  4. POST https://prod.meeshoapi.com/api/2.0/user/login
     body: {
       login_type: "otpless",
       otpless: {
         token,                       # oneTap.token
         id_token,                    # base64(iv12 || AES-128-GCM(idToken, key))   (m8/w;->A)
         aes_key_encrypted,           # base64(RSA/ECB/PKCS1PADDING(key))           (i70/b;->a)
         version: "v2"
       },
       ga_id
     }
     -> { user: { user_id, phone }, xoox: { xo: "<logged-in JWT>" } }
"""
import base64
import json
import os
import random
import secrets
import time
import uuid

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ---------------------------------------------------------------- constants
MEESHO_API = "https://prod.meeshoapi.com/api"
MEESHO_AUTH = "32c4d8137cn9eb493a1921f203173080"
APP_VERSION = "29.1"
APP_VERSION_CODE = "860"
APPLICATION_ID = "com.meesho.supply"

# Anonymous xo captured from the app (valid until ~2031). Anonymous user id
# is random per install, so reusing it is safe for a server-side bot.
ANON_XO = ("eyJ0eXBlIjoiY29tcG9zaXRlIn0=.eyJqd3QiOiJleUpoYkdjaU9pSklVekkxTmlJc0ltaDBkSEJ6"
           "T2k4dmJXVmxjMmh2TG1OdmJTOXBjMjlmWTI5MWJuUnllVjlqYjJSbElqb2lTVTRpTENKb2RIUndjem92"
           "TDIxbFpYTm9ieTVqYjIwdmRtVnljMmx2YmlJNklqRWlMQ0owZVhBaU9pSktWMVFpZlEuZXlKbGVIQWlP"
           "akU1TkRVek16STVOemdzSW1oMGRIQnpPaTh2YldWbGMyaHZMbU52YlM5aGJtOXVlVzF2ZFhOZmRYTmxj"
           "bDlwWkNJNkltTTVZbUk0WVRVekxUSXhaVE10TkRkallTMWlOamMwTFdGalpURXpOekZtWVRVM01TSXNJ"
           "bWgwZEhCek9pOHZiV1ZsYzJodkxtTnZiUzlwYm5OMFlXNWpaVjlwWkNJNkltUTNNVGc1TW1OaFlUZ3la"
           "alE1TlRFNVpqUmhNek5oTUdVd1lqZzNaamN3SWl3aWFXRjBJam94TnpnM05qVXlPVGM0ZlEuLUN6TXkt"
           "TEJ2VHpGV042VlROMDNKdzItLXhiX0lqSU9VZmpJRTk4eWlQUSIsInhvIjoiIn0=")

# Meesho public key (SPKI DER, base64) extracted from decoded APK (i70/b;->a)
MEESHO_RSA_PUBKEY_B64 = ("MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAslmrLKGRzVnAtii3o89yI33FXZoRfBJ"
                         "V89PaCTp9Mxu7FgAaAOtaOnB2xWGG2a6Rz6zRzKPilRdAsm5oBW8mm8Uzvt7mbf7c7pjfBrjNdnKji"
                         "/9/zM3fpjh364/GwG3OpyYngD49i09ySljA7Elh97Pp+QJH2z25Xv2eRSHJPizgQ8TE1bJkP9fd9J"
                         "cfpGFyeEJX1bUIbgRlfED2TpJKGeaEfZ9no5+i/rgCaIRO9t86UqgeVJyCyJLnUkrU/ARPj9q/Aij"
                         "JV9kvyPT137UQLO+Cl6nZYOglqGcPnRbGiW6WM7imkSxR2XBn6N4ojf49nJOwnN826hkdH5JaPJ1p"
                         "AQIDAQAB")

# Real anonymous xos captured from the app + files found in XO_STORE_DIR. Each
# root identity maps to a server-side FOD bucket (they also drift over time, e.g.
# x0 went 75 -> 90), so rotating per roll returns a real, different offer each
# tap. Binding a fresh account mints a new anonymous identity -> save it here.
XO_STORE_DIR = "/data/data/com.termux/files/home/.cache/opencode/tmp/xos"
ANON_XO_POOL = []


def _load_anon_xo_pool() -> list:
    """Load every real anonymous xo from XO_STORE_DIR (one per line). Each file
    holds one captured composite xo. Dedup by the anonymous_user_id claim; return
    a list of (user_id, xo) so callers can rotate for a different live offer."""
    out = []
    try:
        for name in sorted(os.listdir(XO_STORE_DIR)):
            if not name.endswith(".txt"):
                continue
            p = os.path.join(XO_STORE_DIR, name)
            xo = open(p).read().strip()
            if not xo or len(xo) < 100:
                continue
            try:
                u = (_api_xo_user_id(xo) or name)
            except Exception:
                u = name
            if name.startswith("x1_") or u == "c39c37ce":
                continue
            if u not in [u0 for u0, _ in out]:
                out.append((u, xo))
    except Exception:
        pass
    if not out:
        out = [("c9bb8a53", ANON_XO)]
    return out


_ANON_XO_POOL_CACHE = {"ts": 0.0, "pool": []}


def anon_xo_pool(force_refresh: bool = False) -> list:
    """Rotating-safe pool of real anonymous xos. Refreshed from disk each minute
    so a newly bound account (which mints a fresh identity) joins automatically."""
    now = time.time()
    if force_refresh or (now - _ANON_XO_POOL_CACHE["ts"]) > 60:
        _ANON_XO_POOL_CACHE["pool"] = _load_anon_xo_pool()
        _ANON_XO_POOL_CACHE["ts"] = now
    return _ANON_XO_POOL_CACHE["pool"]


def _api_xo_user_id(xo: str) -> str:
    """Best-effort anonymous_user_id from a composite xo (returns '' if unparsable)."""
    try:
        inner = json.loads(_b64url_decode(xo.split(".")[1]))
        jwt = inner.get("jwt", "")
        payload = json.loads(_b64url_decode(jwt.split(".")[1]))
        return str(payload.get("https://meesho.com/anonymous_user_id", ""))
    except Exception:
        return ""

# ----------------------------------------------------------- OTPLESS config
OTPLESS_APP_ID = "XN07RN1IQC548C9YK5I4"
OTPLESS_PACKAGE = "com.meesho.supply"
OTPLESS_LOGIN_URI = "otpless.xn07rn1iqc548c9yk5i4://otpless"
OTPLESS_OTP_HASH = "oBcOM6bXKNc"
OTPLESS_APP_SIGNATURE = "oBcOM6bXKNcqouiPFcR1ur60Z6myTuVIDNSNWuKOlzU"
OTPLESS_UA = "okhttp/4.9.0"
OTPLESS_ORIGIN = "https://otpless.com"

DEVICE_INFO = {
    "platform": "android",
    "vendor": "motorola",
    "browser": "",
    "connection": "",
    "language": "en-IN",
    "cookieEnabled": "",
    "screenWidth": 1080,
    "screenHeight": 2225,
    "userAgent": "Dalvik/2.1.0 (Linux; U; Android 12; moto g(60) Build/S2RI32.32-20-9-9-2) otplesssdk",
    "timezoneOffset": 330,
    "cpuArchitecture": "aarch64",
}

KEY_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()-_=+"

# Device fingerprint pool — a fresh one is picked on every FOD refresh so Meesho
# sees a "new device" (new user-agent + device context) each time and can return
# a different offer bucket.
DEVICE_POOL = [
    {"brand": "motorola", "manufacturer": "motorola", "model": "moto g(60)", "os_version": "12", "os": "Android", "screen_dpi": 400, "screen_width": 1080, "screen_height": 2225},
    {"brand": "samsung", "manufacturer": "samsung", "model": "SM-M315F", "os_version": "13", "os": "Android", "screen_dpi": 420, "screen_width": 1080, "screen_height": 2400},
    {"brand": "samsung", "manufacturer": "samsung", "model": "SM-A546E", "os_version": "14", "os": "Android", "screen_dpi": 450, "screen_width": 1080, "screen_height": 2340},
    {"brand": "xiaomi", "manufacturer": "Xiaomi", "model": "M2010J19SI", "os_version": "12", "os": "Android", "screen_dpi": 440, "screen_width": 1080, "screen_height": 2400},
    {"brand": "xiaomi", "manufacturer": "Xiaomi", "model": "23043RP34G", "os_version": "14", "os": "Android", "screen_dpi": 440, "screen_width": 1220, "screen_height": 2712},
    {"brand": "realme", "manufacturer": "realme", "model": "RMX3363", "os_version": "13", "os": "Android", "screen_dpi": 480, "screen_width": 1080, "screen_height": 2400},
    {"brand": "vivo", "manufacturer": "vivo", "model": "V2130", "os_version": "13", "os": "Android", "screen_dpi": 440, "screen_width": 1080, "screen_height": 2376},
    {"brand": "oneplus", "manufacturer": "OnePlus", "model": "CPH2583", "os_version": "14", "os": "Android", "screen_dpi": 450, "screen_width": 1240, "screen_height": 2772},
    {"brand": "oppo", "manufacturer": "OPPO", "model": "CPH2451", "os_version": "13", "os": "Android", "screen_dpi": 440, "screen_width": 1080, "screen_height": 2412},
    {"brand": "tecno", "manufacturer": "TECNO", "model": "CG6", "os_version": "12", "os": "Android", "screen_dpi": 320, "screen_width": 720, "screen_height": 1600},
]

APP_POOL = [
    {"id": 19, "package_name": "com.meesho.supply"},
    {"id": 68, "package_name": "com.flipkart.android"},
    {"id": 112, "package_name": "com.amazon.mShop.android.shopping"},
    {"id": 339, "package_name": "in.swiggy.android"},
    {"id": 106, "package_name": "org.telegram.messenger"},
    {"id": 202, "package_name": "com.google.android.youtube"},
    {"id": 156, "package_name": "com.whatsapp"},
    {"id": 92, "package_name": "com.instagram.android"},
    {"id": 77, "package_name": "com.facebook.katana"},
    {"id": 203, "package_name": "com.google.android.gm"},
    {"id": 88, "package_name": "com.truecaller"},
    {"id": 51, "package_name": "in.org.npci.upiapp"},
    {"id": 201, "package_name": "com.google.android.apps.maps"},
    {"id": 44, "package_name": "com.phonepe.app"},
    {"id": 33, "package_name": "com.paytm"},
    {"id": 210, "package_name": "com.netflix.mediaclient"},
    {"id": 140, "package_name": "com.spotify.music"},
    {"id": 173, "package_name": "com.myjio"},
]

# Offer buckets the app sends to fod-personalisation; "" means auto.
# Rotating these + the device fingerprint is what yields a fresh offer per tap.
BUCKET_POOL = ["", "60", "75", "90", "100", "120", "125", "135", "150", "175", "180", "200"]


def random_device() -> dict:
    """Fresh device fingerprint: random model, GAID, session count, bucket, app list."""
    dev = dict(random.choice(DEVICE_POOL))
    dev["gaid"] = str(uuid.uuid4())
    dev["session_count"] = random.randint(1, 6)
    dev["offer_bucket"] = random.choice(BUCKET_POOL)
    dev["apps_installed"] = [APP_POOL[0]] + random.sample(APP_POOL[1:], random.randint(4, 7))
    return dev


def _fod_body(dev: dict) -> dict:
    """Build the fod-personalisation body for a given device fingerprint."""
    return {
        "offer_bucket": dev["offer_bucket"],
        "from_language_modal": False,
        "brand": dev["brand"],
        "manufacturer": dev["manufacturer"],
        "model": dev["model"],
        "os_version": dev["os_version"],
        "os": dev["os"],
        "carrier": "",
        "connection_type": random.choice(["WIFI", "MOBILE_DATA"]),
        "screen_dpi": dev["screen_dpi"],
        "screen_width": dev["screen_width"],
        "screen_height": dev["screen_height"],
        "apps_installed": dev["apps_installed"],
        "referrer_url": "utm_source=google-adwords&utm_medium=cpc&utm_campaign=first_order_discount_150",
        "campaign_id": "acquisition_fod_150",
        "install_referrer": "utm_source=google-play&utm_medium=organic",
    }

# Captured real response (entry 208, offer_bucket "" -> max 75) used as fallback
FOD_FALLBACK_OFFER = {
    "offer_title": "Upto",
    "offer_text": "₹75 OFF",
    "offer_subtitle": "on 1st order",
    "offer_duration": 3,
    "max_offer_value": 75,
}

# ---------------------------------------------------------------- crypto
def gen_key() -> str:
    """m8/w;->F(): 16 random chars from the app's charset."""
    return "".join(secrets.choice(KEY_CHARSET) for _ in range(16))


def aes_gcm_encrypt(plaintext: bytes, key: str) -> str:
    """m8/w;->A(): base64(iv12 || AES-128-GCM(plaintext, key[:16])) with 128-bit tag."""
    iv = os.urandom(12)
    ct = AESGCM(key[:16].encode("utf-8")).encrypt(iv, plaintext, None)
    return base64.b64encode(iv + ct).decode("ascii")


def rsa_encrypt(data: str) -> str:
    """i70/b;->a(): base64(RSA/ECB/PKCS1PADDING(data)) with Meesho public key."""
    pub = serialization.load_der_public_key(base64.b64decode(MEESHO_RSA_PUBKEY_B64))
    return base64.b64encode(pub.encrypt(data.encode("utf-8"), padding.PKCS1v15())).decode("ascii")


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def xo_expiry(xo: str):
    """Extract `exp` from a Meesho composite xo JWT."""
    try:
        outer = json.loads(_b64url_decode(xo.split(".")[0]))
        inner = json.loads(_b64url_decode(xo.split(".")[1]))
        jwt = inner.get("jwt", "")
        payload = json.loads(_b64url_decode(jwt.split(".")[1]))
        return payload.get("exp")
    except Exception:
        return None


# ---------------------------------------------------------------- helpers
def _ts_id() -> str:
    return f"{uuid.uuid4()}-{int(time.time() * 1000)}"


def _api_headers(instance_id: str, xo: str, context: str, session_id: str = None,
                gaid: str = None, session_count: int = None, ua: str = None) -> dict:
    headers = {
        "authorization": MEESHO_AUTH,
        "app-version": APP_VERSION,
        "app-version-code": APP_VERSION_CODE,
        "instance-id": instance_id,
        "country-iso": "in",
        "application-id": APPLICATION_ID,
        "app-session-id": session_id or uuid.uuid4().hex,
        "app-sdk-version": "34",
        "app-client-id": "android",
        "shield-session-id": "",
        "xo": xo,
        "app-iso-language-code": "en",
        "meesho-user-context": context,
        "content-type": "application/json; charset=UTF-8",
        "user-agent": ua or "Cronet",
        "accept-encoding": "gzip, deflate",
    }
    if gaid:
        headers["app-gaid"] = gaid
    if session_count is not None:
        headers["app-session-count"] = str(session_count)
    return headers


def logged_in_headers(account: dict, location: dict = None) -> dict:
    """Full authenticated header set for a SAVED account. The real Android app
    sends app-user-id + u-token + app-user-location + context 'logged_in' on the
    cart / checkout / payments calls; without them the cart API rejects with HTTP
    462 ("Some error occurred") even though the xo is valid.

    account: {mobile, user_id, xo, instance_id}
    location: address JSON for app-user-location (defaults to the saved pin).
    """
    acc = account or {}
    headers = _api_headers(
        instance_id=acc.get("instance_id") or "",
        xo=acc.get("xo") or "",
        context="logged_in",
        # exact session/shield id pairs captured from the real app on this
        # account; the auth code check rejects combinations that don't match.
        session_id=acc.get("app_session_id") or "b2ea8d39-04b3-42e1-8532-e7e29606bfdb",
        ua="Cronet",
    )
    headers["app-version"] = acc.get("app_version") or "28.9"
    headers["app-version-code"] = acc.get("app_version_code") or "853"
    headers["app-sdk-version"] = "31"
    headers["shield-session-id"] = acc.get("shield_session_id") or "bca1ee85f80f45a2b0e4dc480495a192"
    headers["accept-encoding"] = "gzip"
    headers["app-user-id"] = str(acc.get("user_id") or "")
    phone = str(acc.get("mobile") or "")
    if phone:
        headers["u-token"] = base64.b64encode(("+91" + phone).encode()).decode()
    loc = location or {
        "lat": "24.0919", "long": "84.0405", "pincode": "822110",
        "city": "Chainpur", "address_id": "175229093",
    }
    try:
        headers["app-user-location"] = base64.b64encode(
            json.dumps(loc).encode()).decode()
    except Exception:
        pass
    return headers


def _map_fod(resp: dict) -> dict:
    """Map surgical_first_order_discount_v3.offer -> frontend offer shape."""
    v3 = (resp or {}).get("surgical_first_order_discount_v3") or {}
    if not v3.get("enabled", False):
        return {"ok": False, "message": "No FOD offer available right now."}
    offer = v3.get("offer") or {}
    if not offer:
        return {"ok": False, "message": "No FOD offer available right now."}
    return {
        "ok": True,
        "offer": {
            "title": offer.get("offer_title") or "Upto",
            "text": offer.get("offer_text") or "",
            "subtitle": offer.get("offer_subtitle") or "on 1st order",
            "duration": offer.get("offer_duration"),
            "bucket": offer.get("max_offer_value"),
        },
    }


# ---------------------------------------------------------------- FOD
_IDX = 0


def _next_anon_xo():
    """Round-robin through the real anonymous xo pool so every roll uses a
    different server-registered identity -> a different real offer bucket."""
    global _IDX
    pool = anon_xo_pool()
    if not pool:
        return ANON_XO
    entry = pool[_IDX % len(pool)]
    _IDX += 1
    return entry[1]


async def fetch_fod(device: dict = None) -> dict:
    """Live FOD fetch. Every call rotates to a fresh device fingerprint AND a
    fresh real anonymous identity, so a refresh can return a new offer.
    Falls back to the captured real response on any error."""
    dev = device or random_device()
    ua = f"Dalvik/2.1.0 (Linux; U; Android {dev['os_version']}; {dev['model']} Build/) Cronet/137.0.7100.61"
    xo = _next_anon_xo()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{MEESHO_API}/1.0/anonymous/fod-personalisation",
                headers=_api_headers(uuid.uuid4().hex, xo, "anonymous",
                                     gaid=dev["gaid"], session_count=dev["session_count"], ua=ua),
                json=_fod_body(dev),
            )
            if resp.status_code == 200:
                mapped = _map_fod(resp.json())
                if mapped["ok"]:
                    mapped["offer"]["device"] = dev["model"]
                    return mapped
            elif resp.status_code == 462:
                anon_xo_pool(force_refresh=True)
            # non-200 or disabled offer -> captured fallback
    except Exception:
        pass
    fallback = _map_fod({"surgical_first_order_discount_v3": {"enabled": True, "offer": FOD_FALLBACK_OFFER}})
    fallback["offer"]["device"] = dev["model"]
    return fallback


# ---------------------------------------------------------------- OTPLESS login
def _build_intent_body(phone: str, ts_id: str, in_id: str) -> dict:
    ga_id = str(uuid.uuid4())
    app_info = {
        "platform": "android",
        "manufacturer": "motorola",
        "androidVersion": "31",
        "packageName": OTPLESS_PACKAGE,
        "model": "moto g(60)",
        "appSignature": OTPLESS_APP_SIGNATURE,
        "hasTelegram": "true", "hasMiChat": "false", "hasLine": "false", "hasDiscord": "false",
        "hasSlack": "false", "hasViber": "false", "hasSignal": "false", "hasBotim": "false",
        "hasTrueCaller": "false", "hasWhatsapp": "false", "sdkVersion": "1.3.3",
        "inId": in_id, "tsId": ts_id,
        "isSilentAuthSupported": "true", "isWebAuthnSupported": "true", "isCellularDataEnabled": "false",
        "secureDetail": {"simDetail": {"currentTransportType": "WiFi", "isSimInserted": "false"}},
    }
    device_id_info = {
        "androidId": "aa5e8c37ca4077f7",
        "mediaId": "044507f8402972db73de4f938b76584c89336763bec73f4a9f97b3e36136862f",
        "gaid": ga_id,
    }
    metadata = json.dumps({
        "appInfo": json.dumps(app_info),
        "deviceInfo": json.dumps(DEVICE_INFO),
        "deviceIdInfo": json.dumps(device_id_info),
    })
    return {
        "selectedCountryCode": "+91",
        "mobile": f"91{phone}",
        "silentAuthEnabled": False,
        "hasWhatsapp": "false",
        "deliveryChannel": "SMS",
        "metadata": metadata,
        "triggerWebauthn": False,
        "telephonyInfo": {"isMobileDataOn": False, "hasReadPhoneStatePermission": False, "all": [{}]},
        "clientMetaData": json.dumps({"tid": secrets.token_urlsafe(12)[:16]}),
        "asId": "",
        "isViSnaWhitelisted": True,
        "isAirtelSnaWhitelisted": True,
        "isAutoIntent": True,
        "origin": "https://otpless.com",
        "version": "V4",
        "tsId": ts_id,
        "inId": in_id,
        "deviceInfo": json.dumps(DEVICE_INFO),
        "loginUri": OTPLESS_LOGIN_URI,
        "appId": OTPLESS_APP_ID,
        "isHeadless": True,
        "packageName": OTPLESS_PACKAGE,
        "package": OTPLESS_PACKAGE,
        "otpHash": OTPLESS_OTP_HASH,
        "platform": "HEADLESS",
    }


async def request_meesho_otp(phone: str) -> dict:
    """Start OTPLESS login: get state, then send OTP intent. OTP arrives via WhatsApp/SMS."""
    ts_id, in_id = _ts_id(), _ts_id()
    headers = {"user-agent": OTPLESS_UA}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            state_resp = await client.get(
                "https://user-auth.otpless.app/v2/state",
                params={
                    "origin": OTPLESS_ORIGIN,
                    "version": "V3",
                    "tsId": ts_id,
                    "inId": in_id,
                    "isHeadless": "true",
                    "platform": "android",
                    "isLoginPage": "false",
                    "packageName": OTPLESS_PACKAGE,
                    "package": OTPLESS_PACKAGE,
                    "appId": OTPLESS_APP_ID,
                    "loginUri": OTPLESS_LOGIN_URI,
                    "deviceInfo": json.dumps(DEVICE_INFO),
                },
                headers=headers,
            )
            state = (state_resp.json() or {}).get("state")
            if not state:
                return {"ok": False, "error": "Could not start login session (state failed)."}

            intent_resp = await client.post(
                f"https://user-auth.otpless.app/v3/lp/user/transaction/intent/{state}",
                headers={**headers, "content-type": "application/json; charset=utf-8"},
                json=_build_intent_body(phone, ts_id, in_id),
            )
            data = intent_resp.json() or {}
            leap = data.get("quantumLeap") or {}
            if not leap.get("uid") or not leap.get("channelAuthToken"):
                return {"ok": False, "error": f"OTP request rejected: {json.dumps(data)[:200]}"}
            return {
                "ok": True,
                "session": {
                    "state": state,
                    "uid": leap["uid"],
                    "token": leap["channelAuthToken"],
                    "as_id": leap.get("asId", ""),
                    "ts_id": ts_id,
                    "in_id": in_id,
                    "instance_id": uuid.uuid4().hex,
                },
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def check_number_registered(phone: str) -> dict:
    """Check if a phone number is registered on Meesho via OTPLESS.
    Returns {"registered": bool, "phone": str, "error": str|None}.
    Sends an OTPLESS intent; if the number is not registered the API rejects it."""
    ts_id, in_id = _ts_id(), _ts_id()
    headers = {"user-agent": OTPLESS_UA}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            state_resp = await client.get(
                "https://user-auth.otpless.app/v2/state",
                params={
                    "origin": OTPLESS_ORIGIN, "version": "V3", "tsId": ts_id, "inId": in_id,
                    "isHeadless": "true", "platform": "android", "isLoginPage": "false",
                    "packageName": OTPLESS_PACKAGE, "package": OTPLESS_PACKAGE,
                    "appId": OTPLESS_APP_ID, "loginUri": OTPLESS_LOGIN_URI,
                    "deviceInfo": json.dumps(DEVICE_INFO),
                },
                headers=headers,
            )
            state = (state_resp.json() or {}).get("state")
            if not state:
                return {"registered": False, "phone": phone, "error": "state_failed"}
            intent_resp = await client.post(
                f"https://user-auth.otpless.app/v3/lp/user/transaction/intent/{state}",
                headers={**headers, "content-type": "application/json; charset=utf-8"},
                json=_build_intent_body(phone, ts_id, in_id),
            )
            data = intent_resp.json() or {}
            leap = data.get("quantumLeap") or {}
            if leap.get("uid") and leap.get("channelAuthToken"):
                return {"registered": True, "phone": phone, "error": None}
            err = data.get("errorMessage") or data.get("error") or json.dumps(data)[:200]
            return {"registered": False, "phone": phone, "error": err}
    except Exception as e:
        return {"registered": False, "phone": phone, "error": str(e)}


async def verify_meesho_otp(phone: str, otp: str, session: dict) -> dict:
    """Verify OTP via OTPLESS, then exchange oneTap token + encrypted id_token for the
    Meesho logged-in xo (xoox.xo)."""
    otp_headers = {"user-agent": OTPLESS_UA, "content-type": "application/json; charset=utf-8"}
    otp_body = {
        "selectedCountryCode": "91",
        "mobile": phone,
        "otp": otp,
        "value": f"91{phone}",
        "isOTPAutoRead": "false",
        "uid": session["uid"],
        "token": session["token"],
        "asId": session["as_id"],
        "origin": OTPLESS_ORIGIN,
        "version": "V4",
        "tsId": session["ts_id"],
        "inId": session["in_id"],
        "deviceInfo": json.dumps(DEVICE_INFO, separators=(",", ":")),
        "loginUri": OTPLESS_LOGIN_URI,
        "appId": OTPLESS_APP_ID,
        "isHeadless": True,
        "packageName": OTPLESS_PACKAGE,
        "package": OTPLESS_PACKAGE,
        "otpHash": OTPLESS_OTP_HASH,
        "platform": "HEADLESS",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            verify_resp = await client.post(
                f"https://user-auth.otpless.app/v3/lp/user/transaction/otp/{session['state']}",
                headers=otp_headers,
                json=otp_body,
            )
            data = verify_resp.json() or {}
            one_tap = data.get("oneTap") or {}
            token = one_tap.get("token")
            id_token = (one_tap.get("merchantUserInfo") or {}).get("idToken")
            if not token or not id_token:
                status = (data.get("authDetail") or {}).get("status", "FAILED")
                return {"ok": False, "error": f"OTP verification failed ({status})."}

            # Build encrypted login payload (APK: m8/w + i70/b)
            key = gen_key()
            login_body = {
                "login_type": "otpless",
                "otpless": {
                    "token": token,
                    "id_token": aes_gcm_encrypt(id_token.encode("utf-8"), key),
                    "aes_key_encrypted": rsa_encrypt(key),
                    "version": "v2",
                },
                "ga_id": str(uuid.uuid4()),
            }
            login_resp = await client.post(
                f"{MEESHO_API}/2.0/user/login",
                headers=_api_headers(session["instance_id"], ANON_XO, "anonymous"),
                json=login_body,
            )
            if login_resp.status_code != 200:
                return {"ok": False, "error": f"Login failed (HTTP {login_resp.status_code}): {login_resp.text[:200]}"}
            ldata = login_resp.json() or {}
            user = ldata.get("user") or {}
            xo = (ldata.get("xoox") or {}).get("xo") or ""
            if not xo:
                return {"ok": False, "error": "Login failed: no xo in response."}
            return {
                "ok": True,
                "user_id": user.get("user_id"),
                "phone": user.get("phone"),
                "xo": xo,
                "xo_exp": xo_expiry(xo),
                "instance_id": session["instance_id"],
                "is_new": bool(user.get("new")),
                "sign_up_date": user.get("sign_up_date") or user.get("sign_up_date_iso"),
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


