from pathlib import Path

ROOT = Path(__file__).resolve().parent

def once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {n}")
    return text.replace(old, new)

app_path = ROOT / "app.py"
html_path = ROOT / "index.html"
app = app_path.read_text(encoding="utf-8")
html = html_path.read_text(encoding="utf-8")

app = once(app, "from fastapi import FastAPI, Request", "from fastapi import FastAPI, Request, Response", "fastapi import")
app = once(app, "\n\ndef _secret():", "\n\nSESSION_COOKIE = \"sj_session\"\ntry:\n    SESSION_TTL_SECONDS = max(86400, int(os.getenv(\"SESSION_TTL_SECONDS\", str(30 * 86400))))\nexcept (TypeError, ValueError):\n    SESSION_TTL_SECONDS = 30 * 86400\n\n\ndef _secret():", "session constants")
app = once(app, "def _issue_token(username, ttl=7 * 86400):", "def _issue_token(username, ttl=None):", "token ttl")
app = once(app, '    """Signed session token (user.exp.hmac). No server-side session store."""\n', '    """Signed session token (user.exp.hmac). No server-side session store."""\n    if ttl is None:\n        ttl = SESSION_TTL_SECONDS\n', "token body")
app = once(app, "\n\ndef _verify_token(token):", "\n\ndef _set_session_cookie(response, token):\n    if response is None or not token:\n        return\n    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, httponly=True, secure=True, samesite=\"lax\", path=\"/\")\n\n\ndef _verify_token(token):", "cookie helper")
app = once(app, '    token = request.headers.get("X-Session", "") or request.headers.get("X-Tg-Init-Data", "")\n    username = _verify_token(token)\n', '    token_candidates = [request.headers.get("X-Session", ""), request.cookies.get(SESSION_COOKIE, ""), request.headers.get("X-Tg-Init-Data", "")]\n    token = ""\n    username = None\n    for candidate in token_candidates:\n        candidate = (candidate or "").strip()\n        if not candidate:\n            continue\n        username = _verify_token(candidate)\n        if username:\n            token = candidate\n            break\n', "auth middleware")
app = once(app, "async def api_auth_login(data: dict = None):", "async def api_auth_login(data: dict = None, request: Request = None, response: Response = None):", "login signature")
app = once(app, '    return {"ok": True, "token": _issue_token(username),\n            "user": {"username": username, "role": u.get("role"),\n', '    token = _issue_token(username)\n    _set_session_cookie(response, token)\n    return {"ok": True, "token": token,\n            "user": {"username": username, "role": u.get("role"),\n', "login cookie")
app = once(app, "async def api_auth_me():\n    u = _current_user()\n    if not u:\n        return {\"authenticated\": False}\n    return _auth_me(u)\n", "async def api_auth_me(response: Response = None):\n    u = _current_user()\n    if not u:\n        return {\"authenticated\": False}\n    token = _issue_token(u.get(\"username\"))\n    _set_session_cookie(response, token)\n    out = _auth_me(u)\n    out[\"token\"] = token\n    return out\n", "auth me")
app = once(app, "async def api_auth_otp_verify(data: dict = None, request: Request = None):", "async def api_auth_otp_verify(data: dict = None, request: Request = None, response: Response = None):", "otp signature")
app = once(app, "async def api_auth_json_login(data: dict = None, request: Request = None):", "async def api_auth_json_login(data: dict = None, request: Request = None, response: Response = None):", "json signature")
if app.count("    token = _issue_token(un)\n") != 2:
    raise RuntimeError("account token sites changed")
app = app.replace("    token = _issue_token(un)\n", "    token = _issue_token(un)\n    _set_session_cookie(response, token)\n")
app = once(app, "async def api_auth_logout():\n    # stateless sessions — the client simply discards the token\n    return {\"ok\": True, \"message\": \"Logged out.\"}\n", "async def api_auth_logout(response: Response = None):\n    if response is not None:\n        response.delete_cookie(SESSION_COOKIE, path=\"/\")\n    return {\"ok\": True, \"message\": \"Logged out.\"}\n", "logout")

marker = "def _plan(user):\n    name = str((user or {}).get(\"plan\") or \"free\")\n    return PLANS.get(name, PLANS[\"free\"])\n"
access = marker + "\n\ndef _free_access_active(user):\n    try:\n        return float((user or {}).get(\"free_access_until\") or 0) > time.time()\n    except (TypeError, ValueError):\n        return False\n\n\ndef _apply_telegram_grant(user, chat_id):\n    chat_id = str(chat_id or \"\").strip()\n    if not user or not chat_id:\n        return\n    user[\"telegram_chat_id\"] = chat_id\n    settings = _load_settings()\n    grants = settings.get(\"free_access_grants\") or {}\n    try:\n        expiry = float(grants.get(chat_id) or 0)\n    except (TypeError, ValueError):\n        expiry = 0\n    if expiry > time.time():\n        user[\"free_access_until\"] = max(float(user.get(\"free_access_until\") or 0), expiry)\n"
app = once(app, marker, access, "free access helpers")
app = once(app, "def _plan_ok(user):\n    plan = _plan(user)\n", "def _plan_ok(user):\n    if _free_access_active(user):\n        return True, _plan(user), None\n    plan = _plan(user)\n", "free access enforcement")
app = once(app, "    u[\"last_seen\"] = int(time.time())\n    _save_users(users)\n    return {\"ok\": True, \"token\": _issue_token(username),", "    u[\"last_seen\"] = int(time.time())\n    try:\n        _apply_telegram_grant(u, _tg_user_id(request.headers.get(\"X-Tg-Init-Data\") if request else \"\"))\n    except Exception:\n        pass\n    _save_users(users)\n    return {\"ok\": True, \"token\": _issue_token(username),", "login telegram link")
app = once(app, "    token = _issue_token(un)\n    # link the verified Meesho account", "    try:\n        _apply_telegram_grant(user, _tg_user_id(request.headers.get(\"X-Tg-Init-Data\") if request else \"\"))\n    except Exception:\n        pass\n    _save_users(users)\n    token = _issue_token(un)\n    # link the verified Meesho account", "otp telegram link")

admin_marker = '@app.get("/api/admin/users")\n'
admin_extra = '''@app.post("/api/admin/free-access")
async def api_admin_free_access(data: dict = None):
    admin = _current_user()
    if not (admin and admin.get("role") == "admin"):
        return {"error": "admin_only", "message": "Admins only."}
    data = data or {}
    chat_id = str(data.get("telegram_chat_id") or data.get("chat_id") or "").strip()
    try:
        days = int(data.get("days") or 1)
    except (TypeError, ValueError):
        days = 1
    if not chat_id or not chat_id.lstrip("-").isdigit():
        return {"error": "bad_chat_id", "message": "Enter a valid Telegram Chat ID."}
    if days not in (1, 2):
        return {"error": "bad_duration", "message": "Duration must be 1 or 2 days."}
    expiry = time.time() + days * 86400
    settings = _load_settings()
    grants = settings.setdefault("free_access_grants", {})
    grants[chat_id] = max(float(grants.get(chat_id) or 0), expiry)
    users = _load_users()
    matched = None
    for target in users.values():
        if str(target.get("telegram_chat_id") or "") == chat_id:
            target["free_access_until"] = expiry
            matched = target.get("username")
    _save_users(users)
    _save_settings(settings)
    return {"ok": True, "telegram_chat_id": chat_id, "days": days, "expires_at": expiry, "matched_user": matched, "message": "Free access granted."}


@app.get("/api/admin/users")
'''
app = once(app, admin_marker, admin_extra, "admin free access endpoint")
app = once(app, '                    "last_seen": un.get("last_seen"), "devices": len(_user_devices(un)),\n                    "used_today": used["count"], "orders_limit": plan["orders"], "id": un.get("id")})', '                    "last_seen": un.get("last_seen"), "devices": len(_user_devices(un)),\n                    "used_today": used["count"], "orders_limit": plan["orders"], "id": un.get("id"),\n                    "telegram_chat_id": un.get("telegram_chat_id"), "free_access_until": un.get("free_access_until", 0)})', "admin user fields")

# Move online order charging/finalization behind verified payment.
app = once(app, "    _save_users(users)\n\n\ndef _user_devices(user):", "    _save_users(users)\n\n\ndef _charge_order_once(order, user):\n    if not isinstance(order, dict) or order.get(\"usage_charged\"):\n        return\n    _charge_order(user)\n    order[\"usage_charged\"] = True\n\n\ndef _mark_order_paid(order, user):\n    if not isinstance(order, dict):\n        return\n    order[\"status_id\"] = \"STATUS_ID_ORDERED\"\n    order[\"status_text\"] = \"Order Placed\"\n    order[\"status_color\"] = \"#038D63\"\n    _charge_order_once(order, user)\n\n\ndef _user_devices(user):", "payment helpers")
app = once(app, "async def api_order_pay_online(data: dict = None):", "def _qr_image_src(value):\n    value = str(value or \"\").strip()\n    if not value:\n        return \"\"\n    if value.startswith(\"data:image/\") or value.startswith(\"http://\") or value.startswith(\"https://\"):\n        return value\n    return \"data:image/png;base64,\" + value\n\n\n@app.post(\"/api/order/pay_online\")\nasync def api_order_pay_online(data: dict = None):", "qr helper")
s = app.index('@app.post("/api/order/pay_online")')
e = app.index('@app.post("/api/order/payment_status")', s)
online = app[s:e]
online = once(online, '    _charge_order(_usr)\n    return resp_out\n', '    return resp_out\n', "premature charge")
online = online.replace('qr_base64 = qr_b64', 'qr_base64 = _qr_image_src(qr_b64)')
if 'if acc.get("is_first_order"):' in online:
    block='    if acc.get("is_first_order"):\n        acc["is_first_order"] = False\n        acc["order_placed"] = True\n'
    online=online.replace(block,'')
app = app[:s] + online + app[e:]
app_path.write_text(app, encoding="utf-8")


# Frontend: cookies, refresh-token storage, and admin free-access controls.
html = once(html, """    headers,
    body: opts && opts.body ? JSON.stringify(opts.body) : undefined""", """    headers,
    credentials: "same-origin",
    body: opts && opts.body ? JSON.stringify(opts.body) : undefined""", "fetch credentials")
html = once(html, """      SESS.set(SESS.token || (me.token||''), storeSessMe(me));""", """      SESS.set(me.token || SESS.token, storeSessMe(me));""", "token refresh")
needle = """      '<div class="admin-rate-card" style="background:var(--surface2);border:2px solid var(--line);border-radius:18px;padding:14px 16px;margin-bottom:16px">' +"""
card = """      '<div class="admin-free-card" style="background:linear-gradient(135deg,#f3e8ff,#ede9fe);border:2px solid #ddd6fe;border-radius:18px;padding:14px 16px;margin-bottom:16px">' +
        '<div style="font-weight:900;font-size:14px;margin-bottom:6px">🎁 Telegram free access</div>' +
        '<div style="font-size:12.5px;color:var(--ink2);margin-bottom:10px;font-weight:600">Grant temporary access without changing the user plan</div>' +
        '<div style="display:flex;gap:8px;align-items:center"><input class="ai-input" id="adminChatId" placeholder="Telegram Chat ID" inputmode="numeric" style="flex:1;font-weight:800"><select class="ai-input" id="adminFreeDays" style="max-width:92px"><option value="1">1 day</option><option value="2">2 days</option></select><button class="cta btn-md" id="adminGrantFree" style="margin:0;padding:12px 14px;font-size:13px">Grant</button></div>' +
        '<div id="adminFreeHint" style="font-size:12px;color:var(--ink3);margin-top:8px;font-weight:600">Chat ID is stored server-side with expiry.</div>' +
      '</div>' +
""" + needle
html = once(html, needle, card, "admin grant card")
listener = """    const saveRate = document.getElementById('adminSaveRate');"""
listener_add = """    const grantFree = document.getElementById('adminGrantFree');
    if(grantFree) grantFree.addEventListener('click', async ()=>{
      const chat = document.getElementById('adminChatId').value.trim();
      const days = document.getElementById('adminFreeDays').value;
      const r = await api('/api/admin/free-access', {method:'POST', body:{telegram_chat_id:chat, days:days}});
      const hint = document.getElementById('adminFreeHint');
      if(r.ok){ toast('Free access granted for '+days+' day'+(days==='1'?'':'s'),'ok'); if(hint) hint.textContent='Granted until '+new Date(r.expires_at*1000).toLocaleString(); }
      else { toast(r.message || 'Could not grant access','err'); if(hint) hint.textContent=r.message || 'Could not grant access'; }
    });
""" + listener
html = once(html, listener, listener_add, "admin grant handler")
html_path.write_text(html, encoding="utf-8")
print("live patch v2 prepared")
