#!/usr/bin/env python3
"""OffRamp REI site server.

Serves the offramp/ static site (like `python -m http.server`, so directory
redirects keep working) PLUS a real application API:

  Public lead capture (unchanged):
    POST /api/signup, /api/unsubscribe, /api/request-access

  App auth:
    POST /api/auth/signup   {email,password,full_name}
    POST /api/auth/login    {email,password}
    POST /api/auth/logout
    GET  /api/auth/sso?provider=google|apple   (demo SSO stub -> real OAuth is a config flip)
    GET  /api/me

  App data (session-cookie protected):
    GET  /api/search?state=&city=&status=&min_equity=&q=&limit=
    GET  /api/property?id=<uuid>
    POST /api/analyze   {arv,rehab,offer,mortgage_balance,market_value}
    POST /api/lookup    {address}          (REAPI PropertyDetail, cached, free-tier metered)
    POST /api/skiptrace {id} | {address}   (DealMachine, Pro-only, metered, mobile-only)
    GET  /api/export?...same filters as search   (CSV, Pro-only)

Plans:
    free : 100 lookups/mo, no skip-trace, no export
    pro  : 2000 lookups/mo, 250 skip-traces/mo, CSV export <= 2000 rows
"""
import json, os, re, io, csv, time, hmac, base64, hashlib, secrets as _secrets
import functools, sys, threading, urllib.request, urllib.error, urllib.parse
import seo_pages
sys.path.insert(0, "/home/cortextos/cortextos/services/lib")
import court_records  # CourtListener RECAP lookup (Phase 5 #35)
import skiptrace_router  # waterfall: cache -> DM 40711 -> Tracerfy -> REAPI -> DM 23501 (2026-10-03)
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from datetime import datetime, timezone

DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("OFFRAMP_PORT", "8092"))

SB_URL = "https://scnpwjyjbcmbjzwgivlu.supabase.co/rest/v1"
SEC = os.path.expanduser("~/.cortextos/secrets")
SRK = open(os.path.join(SEC, "supabase-service-role-key")).read().strip()
SESSION_SECRET = open(os.path.join(SEC, "offramp-session-secret")).read().strip().encode()

# --- optional data-provider keys (features degrade gracefully if missing) -----
def _read(path):
    try:
        return open(path).read().strip()
    except Exception:
        return ""

_reapi = {}
try:
    _reapi = json.load(open(os.path.join(SEC, "realestateapi-key.json")))
except Exception:
    pass
REAPI_KEY = _reapi.get("api_key", "")
REAPI_BASE = _reapi.get("base", "https://api.realestateapi.com")
REAPI_HEADER = _reapi.get("header", "x-api-key")
DM_KEY = _read(os.path.join(SEC, "dealmachine-skiptrace-api-key"))      # org 23501 "Ted Sanders's Team" (Pro Classic, 30k/mo, resets 7th)
DM_KEY_ALT = _read(os.path.join(SEC, "dealmachine-v2-key"))              # org 40711 "Sell Your House Fast…" (Pro Plus Classic, 60k/mo, resets 15th) — fallback when primary is out of credits
GOOGLE_KEY = _read(os.path.join(SEC, "google-maps-api-key"))
# Google Sign-In (OAuth 2.0 authorization-code flow). Reuses the existing
# revivebuyers web OAuth client. Real SSO is live only when BOTH are present
# AND the redirect URI below is registered on that client in Cloud Console.
GOOGLE_OAUTH_CID = _read(os.path.join(SEC, "google-oauth-revivebuyers-client-id"))
GOOGLE_OAUTH_SECRET = _read(os.path.join(SEC, "google-oauth-revivebuyers-client-secret"))

# Stripe billing - LIVE MODE (flipped 2026-09-27). Payment Links +
# product/price/coupon ids were created fresh in the live account; this wires
# them into the app. Test-mode files (stripe-secret-offramp-test etc.) are
# kept on disk untouched as a rollback reference, not read anymore.
SEO_FLUSH_TOKEN = _read(os.path.join(SEC, "offramp-seo-flush-token"))
STRIPE_SECRET = _read(os.path.join(SEC, "stripe-secret-offramp-live"))
STRIPE_WEBHOOK_SECRET = _read(os.path.join(SEC, "stripe-webhook-offramp-secret-live"))
try:
    STRIPE_IDS = json.load(open(os.path.join(SEC, "stripe-offramp-ids-live.json")))
except Exception:
    STRIPE_IDS = {}

# on-disk cache for proxied property imagery (Street View / satellite)
PHOTO_CACHE = os.path.join(DIR, "cache", "photos")
try:
    os.makedirs(PHOTO_CACHE, exist_ok=True)
except Exception:
    pass

RESEND_KEY = os.environ.get("RESEND_API_KEY", "").strip()
FROM = "OffRamp REI <hello@offramprei.com>"
TG_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TG_CHAT = "7872962153"  # Ted

MAIN_ORG_ID = "df45e2a2-3c18-4279-a1b1-5d4648fd5e5d"

# Credits == lookups. Pro/Premium may run past their credits; each extra credit is
# billed at OVERAGE_CENTS through a Stripe invoice item on their next invoice
# (the app keeps the meter, Stripe only runs the subscription). Founding members
# ($149 legacy price) keep the original allowance for their term.
OVERAGE_CENTS = 35
PLAN_LIMITS = {
    "free":         {"lookups": 25,   "skiptraces": 0,   "export_rows": 0,    "premium_data": False, "overage": False},
    "pro":          {"lookups": 200,  "skiptraces": 200, "export_rows": 2000, "premium_data": False, "overage": True},
    "premium":      {"lookups": 200,  "skiptraces": 200, "export_rows": 5000, "premium_data": True,  "overage": True},
    "pro_founding": {"lookups": 2000, "skiptraces": 250, "export_rows": 2000, "premium_data": False, "overage": False},
}
PAID_PLANS = ("pro", "premium")
PREMIUM_COLS = ("next_of_kin", "deceased_party", "probate_case_number", "bankruptcy_case",
                "bankruptcy_case_title", "bankruptcy_case_link", "bankruptcy_active_stay")


def plan_key(u):
    if u.get("plan") == "pro" and u.get("founding"):
        return "pro_founding"
    return u.get("plan") if u.get("plan") in PLAN_LIMITS else "free"


def limits_for(u):
    return PLAN_LIMITS[plan_key(u)]


def is_paid(u):
    return u.get("plan") in PAID_PLANS


def has_premium(u):
    return limits_for(u)["premium_data"]
# markets we actually have inventory for (drives the app market picker)
MARKETS = ["AZ", "UT", "ID", "MT", "WY"]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STATE_MAP = {"Utah": "UT", "Idaho": "ID", "Montana": "MT", "Arizona": "AZ",
             "Wyoming": "WY", "Nevada": "NV", "Colorado": "CO"}


def notify_telegram(text):
    if not TG_TOKEN:
        return
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=json.dumps({"chat_id": TG_CHAT, "text": text}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15):
            pass
    except Exception as e:
        print(f"[notify] telegram deferred: {e}")


# crude per-IP throttle: max 5 lead-form posts / 10 min
_HITS = {}
def throttled(ip):
    now = time.time()
    q = [t for t in _HITS.get(ip, []) if now - t < 600]
    q.append(now)
    _HITS[ip] = q
    return len(q) > 5


def sb(method, path, body=None, headers=None):
    h = {"apikey": SRK, "Authorization": f"Bearer {SRK}",
         "Content-Type": "application/json", "Accept-Profile": "crm",
         "Content-Profile": "crm"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{SB_URL}{path}", data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else []


# ------------------------------ funnel ---------------------------------------
# Conversion-funnel beacons (Ted 2026-10-03). Each call writes one row to
# crm.offramp_funnel_events from a daemon thread: a request is never slowed and
# a DB hiccup is never surfaced. Events: public_view, app_open, signup,
# trial_start, paid (+ lead = public lead form). Same-visitor dedupe happens at
# query time (funnel_report.py) on session_hint = sha256(ip|user-agent)[:16];
# obvious crawlers get a "bot:" prefix so the report can drop them.
_BOT_UA = re.compile(r"bot|crawl|spider|slurp|facebookexternalhit|preview|headless|python|curl|wget|"
                     r"go-http|java/|lighthouse|monitor|uptime", re.I)


def _client_ip(handler):
    return (handler.headers.get("CF-Connecting-IP")
            or (handler.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            or handler.client_address[0])


def funnel(event, user_id=None, path=None, ref=None, handler=None):
    row = {"event": event, "user_id": user_id, "path": path, "ref": ref}
    try:
        if handler is not None:
            if path is None:
                row["path"] = handler.path.split("?")[0][:200]
            if ref is None:
                r = handler.headers.get("Referer") or ""
                row["ref"] = (urllib.parse.urlparse(r).netloc[:120] or None) if r else None
            ua = handler.headers.get("User-Agent") or ""
            hint = hashlib.sha256(f"{_client_ip(handler)}|{ua}".encode()).hexdigest()[:16]
            row["session_hint"] = ("bot:" + hint) if (not ua or _BOT_UA.search(ua)) else hint
    except Exception:
        pass

    def _post():
        try:
            sb("POST", "/offramp_funnel_events", body=row, headers={"Prefer": "return=minimal"})
        except Exception as e:
            print(f"[funnel] {event} deferred: {e}", flush=True)
    try:
        threading.Thread(target=_post, daemon=True).start()
    except Exception:
        pass


seo_pages.ON_PAGE_VIEW = lambda h: funnel("public_view", handler=h)


# ------------------------------- auth ----------------------------------------
def hash_pw(pw, iterations=120000):
    salt = _secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_pw(pw, stored):
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64u_dec(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_session(uid, email, days=14):
    payload = json.dumps({"uid": uid, "email": email,
                          "exp": int(time.time()) + days * 86400}).encode()
    sig = hmac.new(SESSION_SECRET, payload, hashlib.sha256).digest()
    return f"{_b64u(payload)}.{_b64u(sig)}"


def read_session(token):
    try:
        p_b64, sig_b64 = token.split(".")
        payload = _b64u_dec(p_b64)
        expected = hmac.new(SESSION_SECRET, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64u_dec(sig_b64)):
            return None
        data = json.loads(payload)
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


def verify_stripe_sig(payload_bytes, sig_header, secret, tolerance=300):
    """Verify a Stripe webhook signature (Stripe-Signature header scheme)."""
    if not sig_header or not secret:
        return False
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    ts = parts.get("t")
    v1 = parts.get("v1")
    if not ts or not v1:
        return False
    if abs(time.time() - int(ts)) > tolerance:
        return False
    signed = f"{ts}.".encode() + payload_bytes
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


def sign_state(payload_dict, ttl=600):
    payload = json.dumps({**payload_dict, "exp": int(time.time()) + ttl}).encode()
    sig = hmac.new(SESSION_SECRET, payload, hashlib.sha256).digest()
    return f"{_b64u(payload)}.{_b64u(sig)}"


def read_state(token):
    try:
        p_b64, sig_b64 = token.split(".")
        payload = _b64u_dec(p_b64)
        expected = hmac.new(SESSION_SECRET, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64u_dec(sig_b64)):
            return None
        d = json.loads(payload)
        if d.get("exp", 0) < time.time():
            return None
        return d
    except Exception:
        return None


def google_exchange_code(code, redirect_uri):
    data = urllib.parse.urlencode({
        "code": code, "client_id": GOOGLE_OAUTH_CID,
        "client_secret": GOOGLE_OAUTH_SECRET, "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"}).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def decode_jwt_payload(jwt):
    # id_token comes straight from Google's token endpoint over TLS, so the
    # payload is trusted for our purposes; we only read the email/name claims.
    return json.loads(_b64u_dec(jwt.split(".")[1]))


def get_user_by_email(email):
    rows = sb("GET", f"/offramp_users?select=*&email=eq.{urllib.parse.quote(email)}&limit=1")
    return rows[0] if rows else None


def get_user_by_id(uid):
    rows = sb("GET", f"/offramp_users?select=*&id=eq.{uid}&limit=1")
    return rows[0] if rows else None


def get_user_by_stripe_customer(cid):
    rows = sb("GET", f"/offramp_users?select=*&stripe_customer_id=eq.{urllib.parse.quote(cid)}&limit=1")
    return rows[0] if rows else None


def stripe_get(path):
    req = urllib.request.Request(f"https://api.stripe.com/v1/{path}",
        headers={"Authorization": f"Bearer {STRIPE_SECRET}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def stripe_post(path, params):
    """POST to the Stripe API with form-encoded params (supports nested keys
    like 'line_items[0][price]'). Returns the parsed JSON object."""
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.stripe.com/v1/{path}", data=body,
        headers={"Authorization": f"Bearer {STRIPE_SECRET}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


_FOUNDING_CACHE = {"spots_left": None, "at": 0}

def founding_spots_left():
    """Real founding-tier (FOUNDING12, first 100 subscribers) spots remaining,
    computed from actual Stripe promotion-code redemptions - not a static
    marketing number. Cached 60s so page loads don't hammer Stripe."""
    now = time.time()
    if _FOUNDING_CACHE["spots_left"] is not None and now - _FOUNDING_CACHE["at"] < 60:
        return _FOUNDING_CACHE["spots_left"]
    promo_id = STRIPE_IDS.get("promo_m12")
    left = 100
    if promo_id:
        try:
            p = stripe_get(f"promotion_codes/{promo_id}")
            left = max(0, 100 - int(p.get("times_redeemed") or 0))
        except Exception:
            left = 100  # fail open to the full count, never a fabricated low number
    _FOUNDING_CACHE["spots_left"] = left
    _FOUNDING_CACHE["at"] = now
    return left


def ensure_period(user):
    """Reset monthly usage counters at the start of a new calendar month."""
    ps = (user.get("period_start") or "")[:7]
    now = datetime.now(timezone.utc).strftime("%Y-%m")
    if ps != now:
        sb("PATCH", f"/offramp_users?id=eq.{user['id']}",
           body={"lookups_used": 0, "skiptraces_used": 0, "exports_used": 0,
                 "period_start": datetime.now(timezone.utc).strftime("%Y-%m-01")},
           headers={"Prefer": "return=minimal"})
        user.update({"lookups_used": 0, "skiptraces_used": 0, "exports_used": 0})
    return user


def bump(user, field, by=1):
    newval = int(user.get(field) or 0) + by
    sb("PATCH", f"/offramp_users?id=eq.{user['id']}",
       body={field: newval}, headers={"Prefer": "return=minimal"})
    user[field] = newval
    return newval


def public_user(u):
    lim = limits_for(u)
    return {
        "email": u["email"], "full_name": u.get("full_name"), "plan": u["plan"],
        "plan_key": plan_key(u), "founding": bool(u.get("founding")),
        "paid": is_paid(u), "premium": has_premium(u),
        "usage": {
            "lookups": {"used": int(u.get("lookups_used") or 0), "limit": lim["lookups"]},
            "credits": {"used": int(u.get("lookups_used") or 0), "limit": lim["lookups"]},
            "skiptraces": {"used": int(u.get("skiptraces_used") or 0), "limit": lim["skiptraces"]},
            "overage_credits": int(u.get("overage_credits") or 0),
        },
        "overage_cents": OVERAGE_CENTS if lim["overage"] else None,
        "limits": lim,
        "prices": {"pro": 49, "premium": 99},
        "markets": MARKETS,
    }


# --------------------------- data providers ----------------------------------
def norm_addr(a):
    return re.sub(r"\s+", " ", (a or "").strip().lower())


def reapi_lookup(address):
    """REAPI PropertyDetail with enrich-once cache in crm.offramp_lookups."""
    key = norm_addr(address)
    if not key:
        return None, "empty"
    cached = sb("GET", f"/offramp_lookups?select=payload&address_key=eq.{urllib.parse.quote(key)}&limit=1")
    if cached:
        return cached[0]["payload"], "cache"
    if not REAPI_KEY:
        return None, "no-key"
    try:
        req = urllib.request.Request(
            f"{REAPI_BASE}/v2/PropertyDetail",
            data=json.dumps({"address": address}).encode(),
            headers={REAPI_HEADER: REAPI_KEY, "Content-Type": "application/json",
                     "Accept": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=45) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return None, f"reapi {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return None, f"reapi {e}"
    try:
        sb("POST", "/offramp_lookups",
           body={"address_key": key, "payload": payload, "source": "reapi"},
           headers={"Prefer": "return=minimal,resolution=merge-duplicates"})
    except Exception as e:
        print(f"[lookup] cache write deferred: {e}")
    return payload, "live"


def dm_skiptrace(parts):
    """DealMachine enrichment-by-address. `parts` = {street, city, state, zip}.
    Returns (payload, src). Request/response shape per api.docs.dealmachine.com
    (2026-10-03 fix: the old {"address": ...} body was rejected 400 ZodError on every call)."""
    keys = [k for k in (DM_KEY, DM_KEY_ALT) if k]
    if not keys:
        return None, "no-key"
    last = "no-key"
    for key in keys:
        data, last = _dm_enrich_once(key, parts)
        if data is not None:
            return data, "live"
        if last != "vendor-credits":
            break
    return None, last


def _dm_enrich_once(key, parts):
    body = {"data": [{"street": parts.get("street") or "", "city": parts.get("city") or "",
                      "state": parts.get("state") or "", "zip": parts.get("zip") or ""}],
            "fields": ["full_name", "phones", "emails"],
            "contact_audience": "owners_and_family"}
    try:
        req = urllib.request.Request(
            "https://api.v2.dealmachine.com/v1/enrichment/address",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json",
                     "User-Agent": "curl/8.0"}, method="POST")
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        txt = e.read().decode()[:400]
        print(f"[skiptrace] dm HTTP {e.code} (key …{key[-4:]}): {txt}", file=sys.stderr, flush=True)
        if e.code == 402:
            return None, "vendor-credits"
        return None, f"dm {e.code}"
    except Exception as e:
        print(f"[skiptrace] dm error: {e}", file=sys.stderr, flush=True)
        return None, f"dm {e}"
    return data, "live"


def dm_contacts(dm_payload):
    """Flatten the enrichment response: data[].contacts[] -> list of
    {name, type, phones:[{number,type,dnc}], emails:[str]}."""
    out = []
    blocks = (dm_payload or {}).get("data", []) if isinstance(dm_payload, dict) else []
    for block in blocks:
        for c in (block.get("contacts") or []):
            phones = []
            for p in (c.get("phones") or []):
                num = re.sub(r"\D", "", str(p.get("number") or ""))
                if num:
                    phones.append({"number": num, "type": (p.get("type") or "").lower(),
                                   "dnc": bool(p.get("do_not_call") or p.get("dnc"))})
            emails = []
            for e in (c.get("emails") or []):
                ea = e.get("address") or e.get("email") if isinstance(e, dict) else e
                if ea and ea not in emails:
                    emails.append(ea)
            out.append({"name": c.get("full_name") or " ".join(x for x in [c.get("first_name"), c.get("last_name")] if x),
                        "type": (c.get("contact_type") or "owner").lower(),
                        "phones": phones, "emails": emails})
    return out


def extract_mobiles(dm_payload):
    """Mobile/wireless numbers only, per outreach rule (owner + family)."""
    out = []
    seen = set()
    for c in dm_contacts(dm_payload):
        for p in c["phones"]:
            if p["number"] not in seen and any(k in p["type"] for k in ("wireless", "mobile", "cell")):
                seen.add(p["number"])
                out.append({"number": p["number"], "type": p["type"], "dnc": p["dnc"],
                            "name": c["name"], "contact_type": c["type"]})
    return out


def analyze(arv, rehab, offer, mortgage_balance, market_value):
    def f(x):
        try:
            return float(x)
        except Exception:
            return 0.0
    arv, rehab, offer = f(arv), f(rehab), f(offer)
    mb, mv = f(mortgage_balance), f(market_value)
    equity = round((mv or arv) - mb, 0)
    equity_pct = round((equity / (mv or arv) * 100), 1) if (mv or arv) else 0
    profit = round(arv - offer - rehab, 0) if offer else None
    roi = round((profit / (offer + rehab) * 100), 1) if (offer and (offer + rehab)) else None
    return {
        "arv": arv, "rehab": rehab, "offer": offer or None,
        "equity": equity, "equity_pct": equity_pct,
        "est_profit": profit, "roi_pct": roi,
        "verdict": ("Positive spread" if profit is not None and profit > 0
                    else "Negative spread — offer is too high" if profit is not None
                    else "Enter an offer to score the deal"),
    }


# ------------------------------ photos ---------------------------------------
def _google_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "offramp/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(), r.headers.get("Content-Type", "")


def photo_public_path(address):
    addr = (address or "").strip().lower()
    if not addr:
        return None
    ck = hashlib.md5(addr.encode()).hexdigest()
    return f"/cache/photos/{ck}.jpg"


def remember_photo(row_id, address):
    url = photo_public_path(address)
    if not url or not row_id or str(row_id).startswith("nat:"):
        return
    try:
        sb("PATCH", f"/hit_list?id=eq.{urllib.parse.quote(str(row_id))}",
           body={"photo_url": url}, headers={"Prefer": "return=minimal"})
    except Exception as e:
        print(f"[photo] cache write {e}")


def fetch_photo(address):
    """Address-based property image: Street View where a pano exists, else a
    satellite aerial (matches the CRM's photos.ts). Cached on disk."""
    addr = (address or "").strip()
    if not addr or not GOOGLE_KEY:
        return None, None
    ck = hashlib.md5(addr.lower().encode()).hexdigest()
    fp = os.path.join(PHOTO_CACHE, ck + ".jpg")
    ct_fp = fp + ".ct"
    if os.path.exists(fp):
        ct = open(ct_fp).read().strip() if os.path.exists(ct_fp) else "image/jpeg"
        return open(fp, "rb").read(), ct
    q = urllib.parse.quote(addr)
    # Street View metadata is FREE and says whether a pano exists (status OK).
    kind = "sat"
    try:
        s, b, _ = _google_get(
            f"https://maps.googleapis.com/maps/api/streetview/metadata?location={q}&source=outdoor&key={GOOGLE_KEY}")
        if json.loads(b.decode()).get("status") == "OK":
            kind = "street"
    except Exception:
        pass
    if kind == "street":
        url = (f"https://maps.googleapis.com/maps/api/streetview?size=800x420"
               f"&location={q}&fov=80&source=outdoor&key={GOOGLE_KEY}")
    else:
        url = (f"https://maps.googleapis.com/maps/api/staticmap?center={q}"
               f"&zoom=18&size=800x420&maptype=satellite&markers=color:red%7C{q}&key={GOOGLE_KEY}")
    try:
        s, b, ct = _google_get(url)
        if s == 200 and b:
            with open(fp, "wb") as fh:
                fh.write(b)
            with open(ct_fp, "w") as fh:
                fh.write(ct or "image/jpeg")
            return b, ct or "image/jpeg"
    except Exception as e:
        print(f"[photo] {e}")
    return None, None


# ---------------------------- search / rows ----------------------------------
SEARCH_COLS = ("id,owner_full,owner_first,owner_last,property_street,property_city,"
               "property_state,property_zip,county,foreclosure_status,lead_status,"
               "auction_date,auction_time,days_to_auction,market_value,avm,arv,"
               "equity_dollars,equity_pct,ltv_pct,mortgage_balance,beds,baths,"
               "living_area_sqft,year_built,occupancy,property_type,apn,"
               "latitude,longitude,temperature,trustee_file_no,nod_case_number,"
               "is_judicial,foreclosing_attorney,attorney_phone,trustee_opening_bid,"
               "auction_est_value,notice_url,mailing_address,mortgage_lender,"
               "mortgage_interest_rate,mortgage_loan_type,mortgage_recording_date,"
               "mortgage_maturity_date,reverse_mortgage,mls_active,mls_status,"
                "mls_list_price,bankruptcy_flag,bankruptcy_chapter,deceased_flag,"
                "next_of_kin,deceased_party,probate_case_number,bankruptcy_case,"
                "bankruptcy_case_title,bankruptcy_case_link,bankruptcy_active_stay,"
                "skip_traced_at,phones,emails,photo_url")


def build_search_query(qs, limit):
    parts = [f"select={SEARCH_COLS}", "active=eq.true", f"limit={limit}",
             "order=days_to_auction.asc.nullslast"]
    # Deal room defaults to live inventory: hide auctions that already passed
    # (days_to_auction very negative) unless caller explicitly opts in with
    # include_past=1. A short grace window keeps just-passed sales visible.
    if (qs.get("include_past") or [""])[0] != "1":
        parts.append("or=(days_to_auction.gte.-3,days_to_auction.is.null)")
    state = (qs.get("state") or [""])[0].upper().strip()
    if state:
        parts.append(f"property_state=eq.{state}")
    text = (qs.get("q") or qs.get("city") or [""])[0].strip()
    if text:
        # Real search: owner name, street, city, zip, county - one box. A bare
        # 5-digit term matches zip exactly; everything else is a substring match.
        q = urllib.parse.quote(text)
        ors = [f"owner_full.ilike.*{q}*", f"owner_last.ilike.*{q}*", f"property_street.ilike.*{q}*",
               f"property_city.ilike.*{q}*", f"county.ilike.*{q}*"]
        if text.isdigit() and len(text) == 5:
            ors.insert(0, f"property_zip.eq.{q}")
        else:
            ors.append(f"property_zip.ilike.{q}*")
        parts.append("or=(" + ",".join(ors) + ")")
    status = (qs.get("status") or [""])[0].strip()
    if status:
        parts.append(f"foreclosure_status=eq.{urllib.parse.quote(status)}")
    mineq = (qs.get("min_equity") or [""])[0].strip()
    if mineq.isdigit():
        parts.append(f"equity_dollars=gte.{mineq}")
    return "/hit_list?" + "&".join(parts)


NATIONAL_COLS = ("id,listing_id,state,county,county_norm,county_slug,city,zip,street,address,detail_url,"
                  "primary_photo,status,status_group,auction_window,product_type,"
                  "asset_type,occupancy,trustee_sale,beds,baths,sqft,lot_size,"
                  "year_built,est_value,apn,latitude,longitude,auction_date,auction_time_local,"
                  "opening_bid,trustee_sale_number,foreclosing_attorney,foreclosing_attorney_phone,"
                  "venue_name,venue_address")


def national_public_url(r):
    """Canonical public SEO page for a national row (deal room -> public page link).
    Uses the same slug rules as seo_pages so the link always resolves."""
    st = (r.get("state") or "").lower()
    slug_st = seo_pages.ABBR.get(st)
    if not slug_st:
        return None
    return f"/{slug_st}/{seo_pages.row_county_slug(r)}/{seo_pages.listing_slug(r)}"


def build_national_query(qs, limit):
    parts = [f"select={NATIONAL_COLS}", f"limit={limit}",
             "status_group=eq.ACTIVE", "delisted_at=is.null", "order=id.desc"]
    state = (qs.get("state") or [""])[0].upper().strip()
    if state:
        parts.append(f"state=eq.{state}")
    city = (qs.get("city") or [""])[0].strip()
    if city:
        parts.append(f"city=ilike.*{urllib.parse.quote(city)}*")
    county = (qs.get("county") or [""])[0].strip()
    if county:
        parts.append(f"county=ilike.*{urllib.parse.quote(county)}*")
    text = (qs.get("q") or [""])[0].strip()
    if text:  # one box: street, city, county, zip (national rows have no owner name)
        q = urllib.parse.quote(text)
        ors = [f"street.ilike.*{q}*", f"city.ilike.*{q}*", f"county_norm.ilike.*{q}*", f"county.ilike.*{q}*"]
        ors.insert(0, f"zip.eq.{q}") if (text.isdigit() and len(text) == 5) else ors.append(f"zip.ilike.{q}*")
        parts.append("or=(" + ",".join(ors) + ")")
    return "/offramp_national_listings?" + "&".join(parts)


def sb_all(path, page=1000, cap=60):
    """PostgREST caps a response at 1000 rows; page through to get everything."""
    out = []
    for i in range(cap):
        sep = "&" if "?" in path else "?"
        rows = sb("GET", f"{path}{sep}limit={page}&offset={i * page}")
        out.extend(rows)
        if len(rows) < page:
            break
    return out


_STATE_COUNTS = {"at": 0, "val": None}


def state_counts():
    """Active deal counts per state: enriched hit_list + national auction.com feed. Cached 10 min."""
    if _STATE_COUNTS["val"] and time.time() - _STATE_COUNTS["at"] < 600:
        return _STATE_COUNTS["val"]
    hit, nat = {}, {}
    for r in sb_all("/hit_list?select=property_state&active=eq.true&or=(days_to_auction.gte.-3,days_to_auction.is.null)"):
        st = (r.get("property_state") or "").upper()
        if st:
            hit[st] = hit.get(st, 0) + 1
    for r in sb_all("/offramp_national_listings?select=state&status_group=eq.ACTIVE&delisted_at=is.null"):
        st = (r.get("state") or "").upper()
        if st:
            nat[st] = nat.get(st, 0) + 1
    val = {"hit": hit, "national": nat, "total": {k: hit.get(k, 0) + nat.get(k, 0) for k in set(hit) | set(nat)}}
    _STATE_COUNTS.update({"at": time.time(), "val": val})
    return val


def normalize_national_row(r):
    """Map a raw offramp_national_listings row into the same shape the app's
    cards/detail views already expect from hit_list (SEARCH_COLS), so the
    existing UI can render national rows with no separate code path. Owner,
    equity, and skip-trace fields are null - that's the on-click JIT layer,
    not part of the free national base data."""
    return {
        "id": f"nat:{r.get('listing_id')}",
        "owner_full": None, "owner_first": None, "owner_last": None,
        "property_street": r.get("street"), "property_city": r.get("city"),
        "property_state": r.get("state"), "property_zip": r.get("zip"),
        "county": r.get("county_norm") or r.get("county"), "foreclosure_status": r.get("status"),
        "lead_status": None,
        "auction_date": r.get("auction_window"), "auction_time": r.get("auction_time_local"),
        "days_to_auction": None,
        "market_value": r.get("est_value"), "avm": r.get("est_value"), "arv": None,
        "equity_dollars": None, "equity_pct": None, "ltv_pct": None,
        "mortgage_balance": None,
        "beds": r.get("beds"), "baths": r.get("baths"),
        "living_area_sqft": r.get("sqft"), "year_built": r.get("year_built"),
        "occupancy": r.get("occupancy"), "property_type": r.get("structure_type"),
        "apn": r.get("apn"), "latitude": r.get("latitude"), "longitude": r.get("longitude"), "temperature": None,
        "trustee_file_no": r.get("trustee_sale_number"), "nod_case_number": None, "is_judicial": None,
        "foreclosing_attorney": r.get("foreclosing_attorney"), "attorney_phone": r.get("foreclosing_attorney_phone"),
        "trustee_opening_bid": r.get("opening_bid"), "auction_est_value": r.get("est_value"),
        "notice_url": r.get("detail_url"), "mailing_address": None,
        "mortgage_lender": None, "mortgage_interest_rate": None,
        "mortgage_loan_type": None, "mortgage_recording_date": None,
        "mortgage_maturity_date": None, "reverse_mortgage": None,
        "mls_active": None, "mls_status": None, "mls_list_price": None,
        "bankruptcy_flag": None, "bankruptcy_chapter": None, "deceased_flag": None,
        "skip_traced_at": None, "phones": None, "emails": None,
        "contacts_locked": True, "source": "auction.com",
        "primary_photo": r.get("primary_photo"),
        "venue_name": r.get("venue_name"), "venue_address": r.get("venue_address"),
        "public_url": national_public_url(r),
    }


def plan_from_price(price_id):
    """Map a Stripe price id to (plan, founding)."""
    if not price_id:
        return None, False
    if price_id == STRIPE_IDS.get("price_premium99_monthly"):
        return "premium", False
    if price_id == STRIPE_IDS.get("price_pro49_monthly"):
        return "pro", False
    if price_id in (STRIPE_IDS.get("price_monthly"), STRIPE_IDS.get("price_annual")):
        return "pro", True  # legacy $149 founding price
    return None, False


def plan_from_session(sess):
    """Plan for a completed Checkout Session: metadata first, then the line item price."""
    meta_plan = (sess.get("metadata") or {}).get("plan")
    if meta_plan in PAID_PLANS:
        return meta_plan, False
    try:
        items = stripe_get(f"checkout/sessions/{sess.get('id')}/line_items").get("data") or []
        plan, founding = plan_from_price((items[0].get("price") or {}).get("id") if items else None)
        if plan:
            return plan, founding
    except Exception as e:
        print(f"line_items lookup failed: {e}", flush=True)
    return "pro", False


def strip_contacts(row):
    """Free tier: owner phones/emails and the Evaluation dollar figures (equity $, loan balance)
    are withheld server-side; the app renders those rows grayed with a Pro label. Equity %,
    AVM, status, sale date and property facts stay clear."""
    r = dict(row)
    r["phones"] = None
    r["emails"] = None
    r["contacts_locked"] = True
    r["equity_dollars"] = None
    r["mortgage_balance"] = None
    r["evaluation_locked"] = True
    return r


def strip_premium(row):
    """Pro/free never see next-of-kin or PACER bankruptcy tracking (Premium only)."""
    r = dict(row)
    had = any(r.get(c) not in (None, "", [], False) for c in PREMIUM_COLS)
    for c in PREMIUM_COLS:
        r[c] = None
    r["premium_locked"] = True
    r["premium_has_data"] = had
    return r


def gate_row(u, row):
    r = row if is_paid(u) else strip_contacts(row)
    return r if has_premium(u) else strip_premium(r)


def spend_credit(u, kind, ref=None):
    """Meter one credit. Returns (ok, info). Free (or paid without a Stripe customer)
    stops hard at the allowance; Pro/Premium keep going and each credit past the
    allowance becomes a $0.35 Stripe invoice item (billed on their next invoice)."""
    ensure_period(u)
    lim = limits_for(u)
    used = int(u.get("lookups_used") or 0)
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    entry = {"user_id": u["id"], "kind": kind, "qty": 1, "ref": (ref or "")[:200], "period": period,
             "overage": False, "unit_cents": 0}
    if used >= lim["lookups"]:
        if not lim["overage"] or not u.get("stripe_customer_id"):
            return False, {"error": f"monthly credit limit reached ({lim['lookups']})",
                           "upgrade": not is_paid(u), "credits": {"used": used, "limit": lim["lookups"]}}
        desc = f"OffRamp REI credit overage ({kind}) {period}"
        try:
            item = stripe_post("invoiceitems", {"customer": u["stripe_customer_id"], "amount": OVERAGE_CENTS,
                                                "currency": "usd", "description": desc})
            entry.update({"overage": True, "unit_cents": OVERAGE_CENTS, "stripe_invoiceitem_id": item.get("id")})
        except Exception as e:
            print(f"overage invoiceitem error: {e}", flush=True)
            return False, {"error": "could not meter overage credit, try again"}
        bump(u, "overage_credits")
    try:
        sb("POST", "/offramp_credit_ledger", body=entry, headers={"Prefer": "return=minimal"})
    except Exception as e:
        print(f"ledger write error: {e}", flush=True)
    bump(u, "lookups_used")
    return True, {"credits": {"used": int(u["lookups_used"]), "limit": lim["lookups"]},
                  "overage": entry["overage"], "overage_credits": int(u.get("overage_credits") or 0)}


# ------------------------------ HTTP -----------------------------------------
class Handler(SimpleHTTPRequestHandler):
    # ---- helpers ----
    def _json(self, code, obj, extra_headers=None):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return None

    def _current_user(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        c = SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return None
        tok = c.get("offramp_sess")
        if not tok:
            return None
        data = read_session(tok.value)
        if not data:
            return None
        u = get_user_by_id(data["uid"])
        return ensure_period(u) if u else None

    def _session_uid(self):
        # uid from the signed cookie only (no DB hit) -- for funnel beacons
        try:
            c = SimpleCookie()
            c.load(self.headers.get("Cookie") or "")
            tok = c.get("offramp_sess")
            d = read_session(tok.value) if tok else None
            return d.get("uid") if d else None
        except Exception:
            return None

    def _set_session_cookie(self, uid, email):
        tok = make_session(uid, email)
        return ("Set-Cookie",
                f"offramp_sess={tok}; Path=/; Max-Age={14*86400}; "
                f"HttpOnly; Secure; SameSite=Lax")

    def _clear_cookie(self):
        return ("Set-Cookie", "offramp_sess=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")

    # ---- GET ----
    def do_GET(self):
        route = self.path.split("?")[0]
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if route in ("/", "/index.html"):
            try:
                return seo_pages.serve_home(self)
            except Exception as ex:
                print(f"[seo] home inject failed: {ex}")
        elif seo_pages.serve(self, route, qs):
            return
        if not route.startswith("/api/"):
            if route in ("/app", "/app/", "/app/index.html"):
                funnel("app_open", user_id=self._session_uid(), handler=self)
            return super().do_GET()
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if route == "/api/me":
            u = self._current_user()
            if not u:
                return self._json(401, {"error": "not signed in"})
            funnel("app_open", user_id=u["id"], handler=self)  # PWA shell may be sw-cached; deduped per day
            return self._json(200, {"user": public_user(u)})

        if route == "/api/funnel":
            # Ted-only (or pro+founding): the same JSON funnel_report.py renders
            u = self._current_user()
            if not u:
                return self._json(401, {"error": "not signed in"})
            if not (u["email"] == "ted@americahomerestoration.com" or plan_key(u) == "pro_founding"):
                return self._json(403, {"error": "forbidden"})
            try:
                import funnel_report  # lazy: a report bug must never take the site down
                return self._json(200, funnel_report.summary(sb))
            except Exception as ex:
                print(f"[funnel] report error: {ex}", flush=True)
                return self._json(500, {"error": "funnel report unavailable"})

        if route == "/api/founding-count":
            return self._json(200, {"spots_left": founding_spots_left()})

        if route == "/api/auth/sso":
            provider = (qs.get("provider") or ["google"])[0]
            # Real Google OAuth when the client is configured; otherwise demo.
            if provider == "google" and GOOGLE_OAUTH_CID and GOOGLE_OAUTH_SECRET:
                host = (self.headers.get("X-Forwarded-Host")
                        or self.headers.get("Host") or "offramprei.com")
                host = host.split(",")[0].strip()
                redirect_uri = f"https://{host}/api/auth/google/callback"
                state = sign_state({"n": _secrets.token_hex(8), "ru": redirect_uri})
                params = urllib.parse.urlencode({
                    "client_id": GOOGLE_OAUTH_CID, "redirect_uri": redirect_uri,
                    "response_type": "code", "scope": "openid email profile",
                    "state": state, "access_type": "online",
                    "include_granted_scopes": "true", "prompt": "select_account"})
                self.send_response(302)
                self.send_header("Location",
                                 f"https://accounts.google.com/o/oauth2/v2/auth?{params}")
                self.end_headers()
                return
            # Demo SSO fallback (Apple, or Google before the client is wired).
            demo_email = f"demo-{provider}@offramprei.com"
            u = get_user_by_email(demo_email)
            if not u:
                sb("POST", "/offramp_users", body={
                    "email": demo_email, "full_name": f"{provider.title()} Demo User",
                    "plan": "pro", "sso_provider": provider}, headers={"Prefer": "return=minimal"})
                u = get_user_by_email(demo_email)
            sb("PATCH", f"/offramp_users?id=eq.{u['id']}",
               body={"last_login_at": datetime.now(timezone.utc).isoformat()},
               headers={"Prefer": "return=minimal"})
            self.send_response(302)
            self.send_header("Location", "/app/")
            k, v = self._set_session_cookie(u["id"], u["email"])
            self.send_header(k, v)
            self.end_headers()
            return

        if route == "/api/auth/google/callback":
            code = (qs.get("code") or [""])[0]
            st = read_state((qs.get("state") or [""])[0])
            if not code or not st:
                self.send_response(302)
                self.send_header("Location", "/app/?sso=error")
                self.end_headers()
                return
            try:
                tok = google_exchange_code(code, st.get("ru"))
                claims = decode_jwt_payload(tok["id_token"])
                email = (claims.get("email") or "").strip().lower()
                name = (claims.get("name") or "").strip()[:120]
                if not email or not EMAIL_RE.match(email):
                    raise ValueError("no email claim")
            except Exception as e:
                print(f"[sso] google callback error: {e}")
                self.send_response(302)
                self.send_header("Location", "/app/?sso=error")
                self.end_headers()
                return
            u = get_user_by_email(email)
            if not u:
                sb("POST", "/offramp_users", body={
                    "email": email, "full_name": name, "plan": "free",
                    "sso_provider": "google"}, headers={"Prefer": "return=minimal"})
                u = get_user_by_email(email)
                notify_telegram(f"NEW OffRamp SSO signup (google): {email} ({name or 'no name'})")
                funnel("signup", user_id=u["id"], handler=self)
            sb("PATCH", f"/offramp_users?id=eq.{u['id']}",
               body={"last_login_at": datetime.now(timezone.utc).isoformat(),
                     "sso_provider": "google"}, headers={"Prefer": "return=minimal"})
            self.send_response(302)
            self.send_header("Location", "/app/")
            k, v = self._set_session_cookie(u["id"], u["email"])
            self.send_header(k, v)
            self.end_headers()
            return

        if route == "/api/photo":
            # lightweight auth (signed cookie, no DB hit) so <img> tags load fast
            raw = self.headers.get("Cookie") or ""
            c = SimpleCookie()
            try:
                c.load(raw)
            except Exception:
                c = {}
            tok = c.get("offramp_sess") if c else None
            if not tok or not read_session(tok.value):
                return self._json(401, {"error": "not signed in"})
            addr = (qs.get("address") or [""])[0]
            row_id = (qs.get("id") or [""])[0]
            b, ct = fetch_photo(addr)
            if not b:
                return self._json(404, {"error": "no image"})
            remember_photo(row_id, addr)
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Cache-Control", "public, max-age=1209600")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return

        # everything below requires a session
        u = self._current_user()
        if not u:
            return self._json(401, {"error": "not signed in"})

        if route == "/api/state-counts":
            try:
                return self._json(200, state_counts())
            except Exception as ex:
                return self._json(500, {"error": str(ex)[:200]})

        if route == "/api/lead-actions":
            # Item 11: per-user lead state so leads stick between logins.
            try:
                rows = sb("GET", f"/offramp_lead_actions?select=lead_id,saved,hidden,contacted,note,updated_at"
                                 f"&user_id=eq.{urllib.parse.quote(str(u['id']))}&limit=5000")
            except urllib.error.HTTPError as e:
                return self._json(500, {"error": e.read().decode()[:200]})
            return self._json(200, {"actions": {r["lead_id"]: r for r in rows}})

        if route == "/api/search":
            try:
                limit = min(int((qs.get("limit") or ["100"])[0]), 500)
            except Exception:
                limit = 100
            path = build_search_query(qs, limit)
            try:
                rows = sb("GET", path)
            except urllib.error.HTTPError as e:
                return self._json(500, {"error": e.read().decode()[:200]})
            out = [gate_row(u, r) for r in rows]
            return self._json(200, {"count": len(out), "results": out, "plan": u["plan"],
                                    "paid": is_paid(u), "premium": has_premium(u)})

        if route == "/api/national-search":
            # National auction.com base layer (free public data, all 50 states).
            # Owner/equity/skip-trace fields are always null here - those are
            # the JIT-enrichment layer, added on click, not bulk-pulled.
            try:
                limit = min(int((qs.get("limit") or ["100"])[0]), 500)
            except Exception:
                limit = 100
            path = build_national_query(qs, limit)
            try:
                rows = sb("GET", path)
            except urllib.error.HTTPError as e:
                return self._json(500, {"error": e.read().decode()[:200]})
            out = [normalize_national_row(r) for r in rows]
            return self._json(200, {"count": len(out), "results": out, "plan": u["plan"]})

        if route == "/api/property":
            pid = (qs.get("id") or [""])[0]
            if not pid:
                return self._json(400, {"error": "id required"})
            rows = sb("GET", f"/hit_list?select={SEARCH_COLS}&id=eq.{pid}&limit=1")
            if not rows:
                return self._json(404, {"error": "not found"})
            r = gate_row(u, rows[0])
            r["analysis"] = analyze(r.get("arv") or r.get("avm") or r.get("market_value"),
                                    0, None, r.get("mortgage_balance"), r.get("market_value"))
            return self._json(200, {"property": r})

        if route == "/api/export":
            if not is_paid(u):
                return self._json(403, {"error": "CSV export is a Pro feature", "upgrade": True})
            limit = min(limits_for(u)["export_rows"], 5000)
            rows = sb("GET", build_search_query(qs, limit))
            buf = io.StringIO()
            cols = ["owner_full", "property_street", "property_city", "property_state",
                    "property_zip", "county", "foreclosure_status", "auction_date",
                    "days_to_auction", "market_value", "equity_dollars", "equity_pct",
                    "mortgage_balance", "beds", "baths", "living_area_sqft", "year_built",
                    "occupancy", "trustee_file_no", "phones", "emails"]
            w = csv.writer(buf)
            w.writerow(cols)
            for r in rows:
                w.writerow([json.dumps(r.get(c)) if c in ("phones", "emails") else r.get(c, "")
                            for c in cols])
            data = buf.getvalue().encode()
            bump(u, "exports_used")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition",
                             f'attachment; filename="offramp_export_{int(time.time())}.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        return self._json(404, {"error": "not found"})

    # ---- POST ----
    def do_POST(self):
        route = self.path.split("?")[0]

        # ---------- stripe webhook (raw body, verified BEFORE any JSON parse) ----------
        if route == "/internal/seo-flush":
            # Item 12: the daily diff cron asks the live server to drop its page cache
            # + sitemap file so regenerated pages show the new feed. Localhost + token only.
            tok = self.headers.get("X-Seo-Flush-Token", "")
            if self.client_address[0] != "127.0.0.1" or not SEO_FLUSH_TOKEN or not hmac.compare_digest(tok, SEO_FLUSH_TOKEN):
                return self._json(403, {"error": "forbidden"})
            n = len(seo_pages._CACHE)
            seo_pages._CACHE.clear()
            try:
                os.remove(seo_pages.SITEMAP_PATH)
            except OSError:
                pass
            return self._json(200, {"flushed": n, "sitemap_cleared": True})

        if route == "/api/stripe/webhook":
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n)
            sig = self.headers.get("Stripe-Signature", "")
            if not verify_stripe_sig(raw, sig, STRIPE_WEBHOOK_SECRET):
                return self._json(400, {"error": "bad signature"})
            try:
                event = json.loads(raw.decode())
            except Exception:
                return self._json(400, {"error": "bad payload"})
            etype = event.get("type")
            obj = (event.get("data") or {}).get("object") or {}
            try:
                if etype == "checkout.session.completed":
                    uid = obj.get("client_reference_id")
                    cust = obj.get("customer")
                    sub = obj.get("subscription")
                    u = get_user_by_id(uid) if uid else None
                    plan, founding = plan_from_session(obj)
                    if u:
                        sb("PATCH", f"/offramp_users?id=eq.{u['id']}",
                           body={"plan": plan, "founding": founding, "stripe_customer_id": cust,
                                 "stripe_subscription_id": sub, "stripe_status": "active"},
                           headers={"Prefer": "return=minimal"})
                        notify_telegram(f"OffRamp REI: {u['email']} upgraded to {plan.title()} via Stripe checkout")
                        funnel("paid", user_id=u["id"], path="stripe:checkout.session.completed")
                    else:
                        notify_telegram(f"OffRamp REI Stripe checkout completed but no matching user "
                                         f"(client_reference_id={uid}, customer={cust}) — needs manual match")
                elif etype == "customer.subscription.created":
                    # trials are not built yet; record trial_start only if one ever arrives
                    cust = obj.get("customer")
                    u = get_user_by_stripe_customer(cust) if cust else None
                    if u and obj.get("status") == "trialing":
                        funnel("trial_start", user_id=u["id"], path="stripe:customer.subscription.created")
                elif etype == "customer.subscription.updated":
                    cust = obj.get("customer")
                    u = get_user_by_stripe_customer(cust) if cust else None
                    prev_status = ((event.get("data") or {}).get("previous_attributes") or {}).get("status")
                    if u and obj.get("status") == "trialing" and prev_status != "trialing":
                        funnel("trial_start", user_id=u["id"], path="stripe:customer.subscription.updated")
                    if u and obj.get("status") == "active" and prev_status and prev_status != "active":
                        funnel("paid", user_id=u["id"], path="stripe:customer.subscription.updated")
                    if u and obj.get("status") in ("active", "trialing"):
                        plan, founding = plan_from_price(((obj.get("items") or {}).get("data") or [{}])[0].get("price", {}).get("id"))
                        if plan and plan != u.get("plan"):
                            sb("PATCH", f"/offramp_users?id=eq.{u['id']}",
                               body={"plan": plan, "founding": founding, "stripe_status": "active"},
                               headers={"Prefer": "return=minimal"})
                            notify_telegram(f"OffRamp REI: {u['email']} plan changed to {plan.title()}")
                elif etype == "customer.subscription.deleted":
                    cust = obj.get("customer")
                    u = get_user_by_stripe_customer(cust) if cust else None
                    if u:
                        sb("PATCH", f"/offramp_users?id=eq.{u['id']}",
                           body={"plan": "free", "stripe_status": "canceled"},
                           headers={"Prefer": "return=minimal"})
                        notify_telegram(f"OffRamp REI: {u['email']} subscription canceled, moved to Free")
                elif etype == "invoice.paid":
                    cust = obj.get("customer")
                    u = get_user_by_stripe_customer(cust) if cust else None
                    if u and obj.get("billing_reason") in ("subscription_create", "subscription_update"):
                        funnel("paid", user_id=u["id"], path="stripe:invoice.paid")
                elif etype == "invoice.payment_failed":
                    cust = obj.get("customer")
                    u = get_user_by_stripe_customer(cust) if cust else None
                    if u:
                        sb("PATCH", f"/offramp_users?id=eq.{u['id']}",
                           body={"stripe_status": "past_due"}, headers={"Prefer": "return=minimal"})
                        notify_telegram(f"OffRamp REI: payment failed for {u['email']}")
            except Exception as ex:
                notify_telegram(f"OffRamp REI Stripe webhook handler error ({etype}): {str(ex)[:200]}")
            return self._json(200, {"received": True})

        # ---------- billing: build a checkout link for the logged-in user ----------
        if route == "/api/billing/checkout-link":
            u = self._current_user()
            if not u:
                return self._json(401, {"error": "not signed in"})
            data = self._body() or {}
            plan = (data.get("plan") or "pro").strip().lower()
            if plan not in PAID_PLANS:
                return self._json(400, {"error": "unknown plan"})
            price = STRIPE_IDS.get("price_premium99_monthly" if plan == "premium" else "price_pro49_monthly")
            annual = False  # new tiers are monthly only; the $149/$1,490 founding prices stay for existing members
            if not price:
                return self._json(500, {"error": "billing not configured"})
            # Build a Checkout Session server-side (the hosted Payment Link's
            # promo-code box rejects our founding codes; a Session with the
            # discount pre-applied works and auto-applies the founding rate
            # while spots remain, so the buyer never types a code).
            params = {
                "mode": "subscription",
                "line_items[0][price]": price,
                "line_items[0][quantity]": 1,
                "client_reference_id": u["id"],
                "customer_email": u["email"],
                "success_url": "https://offramprei.com/app/?upgraded=1",
                "cancel_url": "https://offramprei.com/app/?upgrade_cancelled=1",
            }
            params["allow_promotion_codes"] = "true"
            params["metadata[plan]"] = plan
            params["subscription_data[metadata][plan]"] = plan
            try:
                sess = stripe_post("checkout/sessions", params)
            except Exception as e:
                print(f"checkout-session error: {e}", flush=True)
                return self._json(502, {"error": "could not start checkout"})
            if not sess.get("url"):
                return self._json(502, {"error": "could not start checkout"})
            return self._json(200, {"url": sess["url"]})

        # ---------- app auth ----------
        if route == "/api/auth/signup":
            data = self._body()
            if data is None:
                return self._json(400, {"error": "bad request"})
            email = (data.get("email") or "").strip().lower()[:160]
            pw = data.get("password") or ""
            name = (data.get("full_name") or "").strip()[:120]
            if not EMAIL_RE.match(email) or len(pw) < 8:
                return self._json(400, {"error": "valid email and 8+ char password required"})
            if get_user_by_email(email):
                return self._json(409, {"error": "an account with that email already exists"})
            sb("POST", "/offramp_users", body={
                "email": email, "password_hash": hash_pw(pw), "full_name": name,
                "plan": "free"}, headers={"Prefer": "return=minimal"})
            u = get_user_by_email(email)
            notify_telegram(f"NEW OffRamp APP signup: {email} ({name or 'no name'})")
            funnel("signup", user_id=u["id"], handler=self)
            return self._json(200, {"user": public_user(u)},
                              extra_headers=[self._set_session_cookie(u["id"], u["email"])])

        if route == "/api/auth/login":
            data = self._body()
            if data is None:
                return self._json(400, {"error": "bad request"})
            email = (data.get("email") or "").strip().lower()[:160]
            pw = data.get("password") or ""
            u = get_user_by_email(email)
            if not u or not u.get("password_hash") or not verify_pw(pw, u["password_hash"]):
                return self._json(401, {"error": "wrong email or password"})
            sb("PATCH", f"/offramp_users?id=eq.{u['id']}",
               body={"last_login_at": datetime.now(timezone.utc).isoformat()},
               headers={"Prefer": "return=minimal"})
            return self._json(200, {"user": public_user(ensure_period(u))},
                              extra_headers=[self._set_session_cookie(u["id"], u["email"])])

        if route == "/api/auth/logout":
            return self._json(200, {"ok": True}, extra_headers=[self._clear_cookie()])

        # ---------- app data (session required) ----------
        if route in ("/api/analyze", "/api/lookup", "/api/skiptrace", "/api/lead-actions", "/api/court-records"):
            u = self._current_user()
            if not u:
                return self._json(401, {"error": "not signed in"})
            data = self._body()
            if data is None:
                return self._json(400, {"error": "bad request"})

            if route == "/api/lead-actions":
                lead_id = str(data.get("id") or "").strip()
                if not lead_id or len(lead_id) > 120:
                    return self._json(400, {"error": "lead id required"})
                body = {"user_id": u["id"], "lead_id": lead_id,
                        "updated_at": datetime.now(timezone.utc).isoformat()}
                for k in ("saved", "hidden", "contacted"):
                    if k in data:
                        body[k] = bool(data[k])
                if "note" in data:
                    note = data.get("note")
                    body["note"] = (str(note)[:4000] if note not in (None, "") else None)
                try:
                    rows = sb("POST", "/offramp_lead_actions?on_conflict=user_id,lead_id", body=body,
                              headers={"Prefer": "resolution=merge-duplicates,return=representation"})
                except urllib.error.HTTPError as e:
                    return self._json(500, {"error": e.read().decode()[:200]})
                r = rows[0] if rows else body
                return self._json(200, {"action": {k: r.get(k) for k in ("lead_id", "saved", "hidden", "contacted", "note", "updated_at")}})

            if route == "/api/analyze":
                return self._json(200, {"analysis": analyze(
                    data.get("arv"), data.get("rehab"), data.get("offer"),
                    data.get("mortgage_balance"), data.get("market_value"))})

            if route == "/api/lookup":
                addr = (data.get("address") or "").strip()
                if not addr:
                    return self._json(400, {"error": "address required"})
                lim = limits_for(u)
                if int(u.get("lookups_used") or 0) >= lim["lookups"] and not (lim["overage"] and u.get("stripe_customer_id")):
                    return self._json(403, {"error": f"monthly credit limit reached ({lim['lookups']})",
                                            "upgrade": not is_paid(u)})
                payload, src = reapi_lookup(addr)
                if payload is None:
                    return self._json(502, {"error": f"lookup failed ({src})"})
                if src == "live":  # only meter fresh pulls, not cache hits
                    ok, info = spend_credit(u, "lookup", addr)
                    if not ok:
                        return self._json(403, info)
                return self._json(200, {"source": src, "data": payload,
                                        "usage": public_user(u)["usage"]})

            if route == "/api/court-records":
                if not is_paid(u):
                    return self._json(403, {"error": "Court records are a Pro feature", "upgrade": True})
                pid = (data.get("id") or "").strip()
                nationwide = bool(data.get("nationwide"))
                if not pid:
                    return self._json(400, {"error": "id required"})
                rows = sb("GET", f"/hit_list?select=id,owner_first,owner_last,owner_full,property_state,court_records&id=eq.{pid}&limit=1")
                if not rows:
                    return self._json(404, {"error": "not found"})
                r = rows[0]
                name = (r.get("owner_full") or " ".join(x for x in [r.get("owner_first"), r.get("owner_last")] if x) or "").strip()
                if not name:
                    return self._json(200, {"source": "none", "reason": "no owner name on file yet", "cases": [], "count": 0})
                cached = r.get("court_records")
                want_scope = "nationwide" if nationwide else (r.get("property_state") or "").upper()
                if isinstance(cached, dict) and court_records.is_fresh(cached) and cached.get("scope") == want_scope and cached.get("query") == name:
                    return self._json(200, dict(cached, source="cache"))
                try:
                    res = court_records.search(name, r.get("property_state"), nationwide=nationwide)
                except Exception as e:
                    print(f"[court-records] {e}", file=sys.stderr, flush=True)
                    return self._json(502, {"error": "Court lookup is temporarily unavailable. You were not charged."})
                if res is None:
                    return self._json(200, {"source": "none", "cases": [], "count": 0})
                try:
                    sb("PATCH", f"/hit_list?id=eq.{pid}", {"court_records": res}, {"Prefer": "return=minimal"})
                except Exception as e:
                    print(f"[court-records] cache write failed {pid}: {e}", file=sys.stderr, flush=True)
                return self._json(200, dict(res, source="live"))
            if route == "/api/skiptrace":
                if not is_paid(u):
                    return self._json(403, {"error": "Skip-trace is a Pro feature", "upgrade": True})
                if plan_key(u) == "pro_founding":
                    lim = limits_for(u)["skiptraces"]
                    if int(u.get("skiptraces_used") or 0) >= lim:
                        return self._json(403, {"error": f"monthly skip-trace limit reached ({lim})"})
                addr = (data.get("address") or "").strip()
                pid = (data.get("id") or "").strip()
                parts = None
                if pid:
                    rows = sb("GET", f"/hit_list?select=id,property_street,property_city,property_state,property_zip,phones,emails,skip_traced_at,owner_first,owner_last,owner_full,skiptrace_attempts&id=eq.{pid}&limit=1")
                    if rows:
                        r = rows[0]
                        # enrich-once: already skip-traced in our DB -> serve from cache, no vendor call, no user credit
                        if r.get("phones") or r.get("skip_traced_at"):
                            ph = r.get("phones") or []
                            return self._json(200, {"source": "cache",
                                                    "mobiles": [p for p in ph
                                                                if any(k in (p.get("type") or "").lower() for k in ("wireless", "mobile", "cell"))],
                                                    "emails": r.get("emails") or []})
                        parts = {"street": r.get("property_street"), "city": r.get("property_city"),
                                 "state": r.get("property_state"), "zip": r.get("property_zip")}
                if parts is None and addr:
                    m = re.match(r"^(.*?),\s*([^,]+?),\s*([A-Za-z]{2})\s+(\d{5})", addr)
                    if not m:
                        return self._json(400, {"error": "address must be 'street, city, ST zip'"})
                    parts = {"street": m.group(1), "city": m.group(2), "state": m.group(3).upper(), "zip": m.group(4)}
                if parts is None:
                    return self._json(400, {"error": "id or address required"})
                row_for_router = dict(parts and {"property_street": parts["street"], "property_city": parts["city"],
                                                 "property_state": parts["state"], "property_zip": parts["zip"]} or {})
                if pid and rows:
                    row_for_router.update({k: rows[0].get(k) for k in ("owner_first", "owner_last", "owner_full")})
                attempts = (rows[0].get("skiptrace_attempts") if (pid and rows) else None) or []
                res = skiptrace_router.trace(row_for_router, attempts)
                if pid:
                    try:
                        sb("PATCH", f"/hit_list?id=eq.{pid}", {"skiptrace_attempts": attempts}, {"Prefer": "return=minimal"})
                    except Exception as e:
                        print(f"[skiptrace] attempts write failed for {pid}: {e}", file=sys.stderr, flush=True)
                if res is None:
                    st = skiptrace_router.status()
                    down = [v for v, x in st.items() if x.get("benched_until")]
                    if down and len(down) >= sum(1 for x in st.values() if x["configured"]):
                        return self._json(503, {"error": "Skip-trace is temporarily unavailable at every data vendor. You were not charged."})
                    return self._json(200, {"source": "live", "mobiles": [], "emails": [], "nohit": True,
                                            "usage": public_user(u)["usage"]})
                contacts = [{"name": c["name"], "type": c["contact_type"], "phones": c["phones"], "emails": c["emails"]} for c in res["contacts"]]
                mobiles = res["mobiles"]
                emails = res["emails"]
                # cache the result on the row so the next viewer costs nothing (enrich-once)
                if pid:
                    try:
                        all_phones = [p for c in contacts for p in c["phones"]]
                        nok = [{"name": c["name"], "relation": c["type"], "phones": c["phones"]}
                               for c in contacts if c["type"] != "owner"]
                        owner = next((c["name"] for c in contacts if c["type"] == "owner" and c["name"]), None)
                        patch = {"phones": all_phones, "emails": emails,
                                 "skip_traced_at": datetime.now(timezone.utc).isoformat(),
                                 "enrichment_source": {"dm40711": "dealmachine", "dm23501": "dealmachine", "tracerfy": "tracerfy", "reapi": "reapi-skiptrace"}.get(res["vendor"], res["vendor"])}
                        if nok:
                            patch["next_of_kin"] = nok
                        if owner:
                            patch["owner_full"] = owner
                        sb("PATCH", f"/hit_list?id=eq.{pid}", patch, {"Prefer": "return=minimal"})
                    except Exception as e:
                        print(f"[skiptrace] cache write failed for {pid}: {e}", file=sys.stderr, flush=True)
                bump(u, "skiptraces_used")
                return self._json(200, {"source": "live", "mobiles": mobiles, "emails": emails,
                                        "usage": public_user(u)["usage"]})

        # ---------- public lead capture (unchanged) ----------
        if route not in ("/api/signup", "/api/unsubscribe", "/api/request-access",
                          "/api/storage-waitlist", "/api/storage-listing"):
            return self._json(404, {"error": "not found"})
        ip = self.headers.get("CF-Connecting-IP") or self.client_address[0]
        if throttled(ip):
            return self._json(429, {"error": "too many requests"})
        data = self._body()
        if data is None:
            return self._json(400, {"error": "bad request"})

        if route == "/api/unsubscribe":
            email = (data.get("email") or "").strip().lower()[:160]
            if not EMAIL_RE.match(email):
                return self._json(400, {"error": "valid email required"})
            try:
                rows = sb("GET", f"/contacts?select=id,tags&email=eq.{urllib.parse.quote(email)}")
                for row in rows:
                    tags = row.get("tags") or []
                    if "unsubscribed" not in tags:
                        tags = tags + ["unsubscribed"]
                    sb("PATCH", f"/contacts?id=eq.{row['id']}",
                       body={"tags": tags}, headers={"Prefer": "return=minimal"})
            except Exception as e:
                print(f"[unsub] error: {e}")
            return self._json(200, {"ok": True})

        if route == "/api/request-access":
            first = (data.get("first") or "").strip()[:80]
            last = (data.get("last") or "").strip()[:80]
            email = (data.get("email") or "").strip().lower()[:160]
            phone = re.sub(r"\D", "", data.get("phone") or "")[-10:]
            market = (data.get("market") or "").strip()[:60]
            hunting = (data.get("hunting") or "").strip()[:600]
            if data.get("company"):
                return self._json(200, {"ok": True})
            if not first or not last or not EMAIL_RE.match(email):
                return self._json(400, {"error": "name and a valid email are required"})
            full_name = f"{first} {last}".strip()
            try:
                dup = sb("GET", f"/access_requests?select=id&email=eq.{urllib.parse.quote(email)}&status=eq.pending&limit=1")
                if not dup:
                    sb("POST", "/access_requests", body={
                        "full_name": full_name, "email": email,
                        "phone": phone or None, "market": market or None,
                        "hunting_for": hunting or None, "status": "pending",
                        "source": "OffRamp REI /login"},
                       headers={"Prefer": "return=minimal"})
                    notify_telegram(
                        "NEW OffRamp access request (pending approval)\n"
                        f"Name: {full_name}\nEmail: {email}\n"
                        f"Phone: {phone or 'n/a'}\nMarket: {market or 'n/a'}\n"
                        f"Hunting: {hunting or 'n/a'}")
            except urllib.error.HTTPError as e:
                print(f"[access] db error: {e.read().decode()[:300]}")
                return self._json(500, {"error": "could not save"})
            except Exception as e:
                print(f"[access] db error: {e}")
                return self._json(500, {"error": "could not save"})
            return self._json(200, {"ok": True})

        if route == "/api/storage-waitlist":
            email = (data.get("email") or "").strip().lower()[:160]
            name = (data.get("name") or "").strip()[:120]
            note = (data.get("note") or "").strip()[:400]
            if data.get("company"):
                return self._json(200, {"ok": True})
            if not EMAIL_RE.match(email):
                return self._json(400, {"error": "valid email required"})
            try:
                dup = sb("GET", f"/contacts?select=id&email=eq.{urllib.parse.quote(email)}&limit=1")
                if not dup:
                    sb("POST", "/contacts", body={
                        "first_name": name or None, "email": email,
                        "type": "buyer", "status": "lead", "lead_pool": "active",
                        "source": "Distressed Self-Storage Data waitlist",
                        "notes": note or "Early-access waitlist signup.",
                        "tags": ["storage-waitlist", "early-access"]},
                       headers={"Prefer": "return=minimal"})
                    notify_telegram(
                        "NEW distressed self-storage waitlist signup\n"
                        f"Email: {email}\nName: {name or 'n/a'}\nNote: {note or 'n/a'}")
            except urllib.error.HTTPError as e:
                print(f"[storage-waitlist] db error: {e.read().decode()[:300]}")
                return self._json(500, {"error": "could not save"})
            except Exception as e:
                print(f"[storage-waitlist] db error: {e}")
                return self._json(500, {"error": "could not save"})
            return self._json(200, {"ok": True})

        if route == "/api/storage-listing":
            name = (data.get("name") or "").strip()[:120]
            email = (data.get("email") or "").strip().lower()[:160]
            phone = re.sub(r"\D", "", data.get("phone") or "")[-10:]
            address = (data.get("address") or "").strip()[:200]
            city = (data.get("city") or "").strip()[:80]
            state = (data.get("state") or "").strip()[:2].upper()
            units = (data.get("units") or "").strip()[:40]
            asking = (data.get("asking") or "").strip()[:60]
            note = (data.get("note") or "").strip()[:600]
            if data.get("company"):
                return self._json(200, {"ok": True})
            if not name or not EMAIL_RE.match(email) or not address:
                return self._json(400, {"error": "name, email, and facility address are required"})
            full_note = (f"Self-submitted storage listing. Units: {units or 'n/a'}. "
                         f"Asking: {asking or 'n/a'}. Notes: {note or 'n/a'}")
            try:
                sb("POST", "/contacts", body={
                    "first_name": name, "email": email, "phone": phone or None,
                    "type": "seller", "status": "lead", "lead_pool": "active",
                    "source": "Distressed Self-Storage Data — list your facility",
                    "property_address": address or None, "city": city or None,
                    "state": state or None, "notes": full_note,
                    "tags": ["storage-seller-lead", "self-submitted"]},
                   headers={"Prefer": "return=minimal"})
                notify_telegram(
                    "NEW self-storage SELLER lead (self-submitted, hot)\n"
                    f"Name: {name}\nEmail: {email}\nPhone: {phone or 'n/a'}\n"
                    f"Facility: {address}, {city}, {state}\n"
                    f"Units: {units or 'n/a'}  Asking: {asking or 'n/a'}\nNote: {note or 'n/a'}")
            except urllib.error.HTTPError as e:
                print(f"[storage-listing] db error: {e.read().decode()[:300]}")
                return self._json(500, {"error": "could not save"})
            except Exception as e:
                print(f"[storage-listing] db error: {e}")
                return self._json(500, {"error": "could not save"})
            return self._json(200, {"ok": True})

        # /api/signup
        first = (data.get("first") or "").strip()[:80]
        last = (data.get("last") or "").strip()[:80]
        email = (data.get("email") or "").strip().lower()[:160]
        phone = re.sub(r"\D", "", data.get("phone") or "")[-10:]
        market = (data.get("market") or "").strip()[:60]
        if data.get("company"):
            return self._json(200, {"ok": True})
        if not first or not last or not EMAIL_RE.match(email):
            return self._json(400, {"error": "name and a valid email are required"})
        state = STATE_MAP.get(market)
        note = f"OffRamp REI signup. Market: {market or 'n/a'}."
        try:
            dup = sb("GET", f"/contacts?select=id&email=eq.{urllib.parse.quote(email)}&limit=1")
            if not dup:
                sb("POST", "/contacts", body={
                    "first_name": first, "last_name": last, "email": email,
                    "phone": phone or None, "type": "buyer", "status": "lead",
                    "lead_pool": "active", "source": "OffRamp Signup",
                    "state": state, "notes": note,
                    "tags": ["offramp-signup", "waitlist"]},
                   headers={"Prefer": "return=minimal"})
        except urllib.error.HTTPError as e:
            print(f"[signup] db error: {e.read().decode()[:300]}")
            return self._json(500, {"error": "could not save"})
        except Exception as e:
            print(f"[signup] db error: {e}")
            return self._json(500, {"error": "could not save"})
        funnel("lead", handler=self)
        send_welcome(first, email)
        return self._json(200, {"ok": True})


def is_unsubscribed(email):
    try:
        rows = sb("GET", f"/contacts?select=tags&email=eq.{urllib.parse.quote(email)}&limit=1")
        return bool(rows) and "unsubscribed" in (rows[0].get("tags") or [])
    except Exception:
        return False


def send_welcome(first, email):
    if not RESEND_KEY or is_unsubscribed(email):
        return False
    html = f"""<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:520px;margin:0 auto;color:#1b2b22">
<h2 style="color:#1B4332">Welcome to OffRamp REI, {first}.</h2>
<p>You're in. OffRamp surfaces every upcoming auction, pre-foreclosure, and probate in your market — with verified equity and skip-traced owner contact on every lead.</p>
<p><a href="https://offramprei.com/learn" style="background:#1B4332;color:#fff;padding:11px 20px;border-radius:8px;text-decoration:none;display:inline-block">Open the Learn hub &rarr;</a></p>
<p>&mdash; The OffRamp REI team</p></div>"""
    body = {"from": FROM, "to": [email],
            "subject": "You're in — welcome to OffRamp REI", "html": html}
    try:
        req = urllib.request.Request("https://api.resend.com/emails",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {RESEND_KEY}",
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30):
            return True
    except Exception as e:
        print(f"[signup] welcome-email deferred: {e}")
        return False


if __name__ == "__main__":
    os.chdir(DIR)
    HandlerBound = functools.partial(Handler, directory=DIR)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), HandlerBound)
    print(f"OffRamp server on 127.0.0.1:{PORT} dir={DIR} "
          f"reapi={'on' if REAPI_KEY else 'off'} dm={'on' if DM_KEY else 'off'}")
    srv.serve_forever()
