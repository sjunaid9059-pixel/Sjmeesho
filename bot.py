import json
import os
import time
import traceback

import httpx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE_DIR, "bot_config.json")


def load_cfg():
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["token"] = (
        os.environ.get("BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
        or cfg.get("token")
        or ""
    ).strip()
    cfg["shop_url"] = (
        os.environ.get("SHOP_URL")
        or os.environ.get("WEBAPP_URL")
        or os.environ.get("PUBLIC_URL")
        or cfg.get("shop_url")
        or ""
    ).rstrip("/")
    return cfg


CFG = load_cfg()
TOKEN = CFG["token"]
SHOP_URL = CFG["shop_url"]
API = "https://api.telegram.org/bot" + TOKEN


def api_call(method, **params):
    r = httpx.post(API + "/" + method, json=params, timeout=60)
    if not r.is_success:
        raise RuntimeError(f"Telegram HTTP {r.status_code} in {method}: {r.text[:300]}")
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description') or data}")
    return data


def current_shop_url():
    try:
        with open(os.path.join(BASE_DIR, "current_tunnel_url"), "r", encoding="utf-8") as f:
            u = f.read().strip()
            if u.startswith("https://"):
                return u
    except Exception:
        pass
    try:
        configured = json.load(open(CFG_PATH, "r", encoding="utf-8")).get("shop_url", "").rstrip("/")
        if configured.startswith("https://") and "YOUR_" not in configured:
            return configured
    except Exception:
        pass
    return SHOP_URL


def webapp_url():
    base = current_shop_url()
    sep = "&" if "?" in base else "?"
    return base + sep + "v=20260901"


def send_start(chat_id, text=None, reply_to=None):
    text = text or CFG.get("welcome_text") or "Open the shop:"
    keyboard = {
        "inline_keyboard": [
            [{"text": "🛍️ Open Shop", "web_app": {"url": webapp_url()}}],
        ]
    }
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": keyboard}
    if reply_to:
        params["reply_to_message_id"] = reply_to
    api_call("sendMessage", **params)


def handle_message(m):
    chat_id = m.get("chat", {}).get("id")
    if not chat_id:
        return
    send_start(chat_id, reply_to=m.get("message_id"))


def main():
    if not TOKEN or TOKEN.startswith("YOUR_"):
        raise RuntimeError("BOT_TOKEN/TELEGRAM_BOT_TOKEN is missing in Render environment")
    print("token_valid=getMe", flush=True)
    me = api_call("getMe")
    print("bot =@" + (me["result"].get("username") or "?"), flush=True)
    try:
        api_call("deleteWebhook", drop_pending_updates=False)
    except Exception:
        pass

    def refresh_menu():
        try:
            api_call("setChatMenuButton", menu_button={
                "type": "web_app", "text": "🛍️ Open Shop",
                "web_app": {"url": webapp_url()}
            })
            return True
        except Exception:
            return False

    try:
        refresh_menu()
        print("menu_button=ok", flush=True)
    except Exception as e:
        print("menu_button=skip (" + str(e) + ")", flush=True)

    last_id = 0
    last_menu_refresh = time.time()
    while True:
        try:
            if time.time() - last_menu_refresh >= 30:
                if refresh_menu():
                    last_menu_refresh = time.time()
            up = api_call("getUpdates", timeout=30, limit=50,
                          offset=last_id + 1 if last_id else 0,
                          allowed_updates=["message", "callback_query"])
            for update in up.get("result", []):
                last_id = max(last_id, update.get("update_id", last_id))
                try:
                    if "message" in update:
                        handle_message(update["message"])
                except Exception:
                    traceback.print_exc()
        except Exception:
            traceback.print_exc()
            print("poll_backoff 5s", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()