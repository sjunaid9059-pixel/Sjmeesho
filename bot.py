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
    cfg["token"] = os.environ.get("BOT_TOKEN", cfg.get("token", ""))
    cfg["shop_url"] = os.environ.get("SHOP_URL", cfg.get("shop_url", "")).rstrip("/")
    return cfg


CFG = load_cfg()
TOKEN = CFG["token"]
SHOP_URL = CFG["shop_url"]
API = "https://api.telegram.org/bot" + TOKEN


def api_call(method, **params):
    r = httpx.post(API + "/" + method, json=params, timeout=60)
    r.raise_for_status()
    return r.json()


def current_shop_url():
    """Re-read the live shop/tunnel URL every time so inline buttons + the webapp
    always point to the CURRENT cloudflared URL, even after it auto-rotates."""
    try:
        with open(os.path.join(BASE_DIR, "current_tunnel_url"), "r", encoding="utf-8") as f:
            u = f.read().strip()
            if u.startswith("https://"):
                return u
    except Exception:
        pass
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("shop_url", SHOP_URL).rstrip("/")
    except Exception:
        return SHOP_URL


def send_start(chat_id, text=None, reply_to=None):
    text = text or CFG.get("welcome_text") or "Open the shop:"
    url = current_shop_url()
    keyboard = {
        "inline_keyboard": [
            [{"text": "🛍️ Open Shop", "web_app": {"url": url}}],
        ]
    }
    api_call("sendMessage", chat_id=chat_id, text=text,
             parse_mode="HTML", reply_markup=keyboard)


def handle_message(m):
    chat_id = m.get("chat", {}).get("id")
    text = (m.get("text") or "").strip().lower()
    if not chat_id:
        return
    if text in ("/start", "/open", "/menu", "/shop", "open shop", "shop", "open"):
        send_start(chat_id)
    else:
        send_start(chat_id)


def main():
    print("token_valid=getMe", flush=True)
    me = api_call("getMe")
    print("bot =@" + (me["result"].get("username") or "?"), flush=True)
    try:
        api_call("deleteWebhook", drop_pending_updates=True)
    except Exception:
        pass

    def refresh_menu():
        try:
            api_call("setChatMenuButton", menu_button={
                "type": "web_app", "text": "🛍️ Open Shop",
                "web_app": {"url": current_shop_url()}
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
            # Re-point the sidebar webapp button whenever the tunnel URL changed
            if time.time() - last_menu_refresh >= 30:
                if refresh_menu():
                    last_menu_refresh = time.time()
            up = api_call("getUpdates", timeout=30, limit=50,
                          offset=last_id + 1 if last_id else -1,
                          allowed_updates=["message", "callback_query"])
            for u in up.get("result", []):
                last_id = max(last_id, u.get("update_id", last_id))
                try:
                    if "message" in u:
                        handle_message(u["message"])
                except Exception:
                    traceback.print_exc()
        except Exception:
            traceback.print_exc()
            print("poll_backoff 5s", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()