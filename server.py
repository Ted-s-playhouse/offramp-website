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
    POST /api/auth/forgot   {email}            (always 200; emails a signed 30-min single-use link)
    POST /api/auth/reset    {token,password}
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
import json, os, re, io, csv, time, hmac, base64, hashlib, secrets as _secrets, posixpath
import functools, sys, threading, urllib.request, urllib.error, urllib.parse
import http.client, queue, gzip
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
# OFFRAMP_STRIPE_MODE=test runs a sandbox instance against the Stripe TEST
# account (used to verify billing changes such as the 7-day trial, item 37)
# without touching live prices, webhooks or customers. pm2 never sets it.
STRIPE_MODE = "test" if os.environ.get("OFFRAMP_STRIPE_MODE", "").strip().lower() == "test" else "live"
if STRIPE_MODE == "test":
    STRIPE_SECRET = _read(os.path.join(SEC, "stripe-secret-offramp-test"))
    STRIPE_WEBHOOK_SECRET = _read(os.path.join(SEC, "stripe-webhook-offramp-secret"))
    _STRIPE_IDS_FILE = "stripe-offramp-ids.json"
else:
    STRIPE_SECRET = _read(os.path.join(SEC, "stripe-secret-offramp-live"))
    STRIPE_WEBHOOK_SECRET = _read(os.path.join(SEC, "stripe-webhook-offramp-secret-live"))
    _STRIPE_IDS_FILE = "stripe-offramp-ids-live.json"
try:
    STRIPE_IDS = json.load(open(os.path.join(SEC, _STRIPE_IDS_FILE)))
except Exception:
    STRIPE_IDS = {}
# 7-day free trial (Phase 5 item 37): card on file, Premium features during the
# trial, converts to Pro $49/mo on day 8. Always on in test mode; live needs
# OFFRAMP_TRIAL_LIVE=1 once Ted has enabled customer.subscription.created +
# invoice.paid on the live webhook endpoint (until then the button stays hidden
# and /api/billing/checkout-link {plan:"trial"} answers 409).
TRIAL_DAYS = int(os.environ.get("OFFRAMP_TRIAL_DAYS", "7") or 7)   # Ted 2026-10-03: 30-day preview at launch (OFFRAMP_TRIAL_DAYS=30)
TRIAL_ENABLED = STRIPE_MODE == "test" or os.environ.get("OFFRAMP_TRIAL_LIVE", "").strip() == "1"
# $5 document pull (Ted 2026-10-03: "a JIT button on the lead. Pick the document.
# Pay five dollars. The actual recorded filing shows up as a PDF."). One-time
# live Price on product prod_VNMSIl9WBB1dYM "OffRamp document pull"; Checkout
# mode=payment with metadata.kind=doc_pull; the webhook writes
# crm.offramp_doc_orders and pings Telegram; Ted fulfils by hand within 24h.
DOC_PULL_PRICE = STRIPE_IDS.get("price_doc_pull") or "price_1UMbjiLq4QlEucPyj79859c9"
DOC_PULL_DOCS = {"mortgage": "Recorded mortgage", "nod": "Notice of default",
                 "lis_pendens": "Lis pendens", "bankruptcy": "Bankruptcy petition"}
DOC_PULL_ADMIN = "ted@americahomerestoration.com"

# ------------------------- security hardening (2026-10-03) --------------------
# Canonical origin for links we put in email (never trust the Host header for that).
APP_ORIGIN = (os.environ.get("OFFRAMP_ORIGIN", "").strip() or "https://offramprei.com").rstrip("/")
RESET_TTL = 30 * 60   # password-reset links live 30 minutes and die on first use

# Login gate for the static deliverable folders (/_<name>/...: mockups, reports, drafts).
# Everything under a top-level "_" folder is admin-only unless the folder is listed in
# public_folders.json (array of folder names, re-read every 30s so no restart is needed).
# ALWAYS_GATED wins over the allowlist.
PUBLIC_FOLDERS_PATH = os.path.join(DIR, "public_folders.json")
ALWAYS_GATED = {"_uat-report-4d1f", "_api-credits-7c2e", "_serial-buyers-2026-10-03-9b4e", "__pycache__"}

# Response headers on every response. CSP ships REPORT-ONLY first (launch weekend): flip
# CSP_REPORT_ONLY to False once /api/csp-report stays quiet. One string so the switch is a
# one-liner. Origins reflect what the shell + public pages really load: Leaflet from unpkg,
# OSM tiles + listing photos as <img>, Google Fonts on the public pages, Google Maps embeds
# in two marketing pages. Inline handlers/styles are everywhere, hence 'unsafe-inline'.
CSP = ("default-src 'self'; "
       "script-src 'self' 'unsafe-inline' https://unpkg.com; "
       "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; "
       "font-src 'self' data: https://fonts.gstatic.com; "
       "img-src 'self' data: blob: https:; "
       "connect-src 'self'; "
       "frame-src https://www.google.com; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'; "
       "report-uri /api/csp-report")
CSP_REPORT_ONLY = True
SECURITY_HEADERS = [
    ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),   # nothing embeds the site: the iOS wrapper is a WebView, the two <iframe>s are outbound maps
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),   # the map is address-pinned; no getCurrentPosition anywhere in app/
    (("Content-Security-Policy-Report-Only" if CSP_REPORT_ONLY else "Content-Security-Policy"), CSP),
]

# on-disk cache for proxied property imagery (Street View / satellite)
PHOTO_CACHE = os.path.join(DIR, "cache", "photos")
GZIP_JSON = os.environ.get("OFFRAMP_GZIP", "1").strip() != "0"   # perf 2026-10-03: gzip JSON over 1 KB at the origin
GZIP_MIN = 1024
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
    # 7-day trial = Premium-level access; no overage billing while trialing
    "trial":        {"lookups": 200,  "skiptraces": 200, "export_rows": 5000, "premium_data": True,  "overage": False},
}
PAID_PLANS = ("pro", "premium")
TRIAL_PLAN = "trial"
TERMS_VERSION = "2026-10-05"   # bump when /terms changes; users re-accept on next entry
PREMIUM_COLS = ("next_of_kin", "deceased_party", "probate_case_number", "bankruptcy_flag", "bankruptcy_chapter", "bankruptcy_case",
                "bankruptcy_case_title", "bankruptcy_case_link", "bankruptcy_active_stay",
                "surplus_amount", "surplus_sale_date", "surplus_purchaser", "surplus_court", "surplus_claim_deadline")


def plan_key(u):
    if u.get("plan") == "pro" and u.get("founding"):
        return "pro_founding"
    return u.get("plan") if u.get("plan") in PLAN_LIMITS else "free"


def limits_for(u):
    return PLAN_LIMITS[plan_key(u)]


def is_paid(u):
    return u.get("plan") in PAID_PLANS or u.get("plan") == TRIAL_PLAN


def trial_eligible(u):
    """One trial per customer ever: free plan, never trialed, feature switched on."""
    return bool(TRIAL_ENABLED and u.get("plan") == "free" and not u.get("trial_used_at"))


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


# perf 2026-10-03: one TCP+TLS handshake per PostgREST call cost ~30 ms of every hop (a lead open made five).
# Idle keep-alive connections are pooled; a stale socket (closed by the far end while idle) is retried once.
_SB_HOST = urllib.parse.urlparse(SB_URL).netloc
_SB_PREFIX = urllib.parse.urlparse(SB_URL).path.rstrip("/")
_SB_POOL = queue.LifoQueue()
_SB_POOL_MAX = 8
_SB_IDLE_MAX = 50.0   # seconds; the far end drops idle sockets around 60 s, never hand out one older than that
_SB_STALE = (http.client.HTTPException, OSError)   # a pooled socket that died while idle surfaces as one of these


def _sb_conn():
    """(connection, reused) - an idle pooled connection or a fresh one."""
    while True:
        try:
            c, last = _SB_POOL.get_nowait()
        except queue.Empty:
            return http.client.HTTPSConnection(_SB_HOST, timeout=30), False
        if time.time() - last < _SB_IDLE_MAX:
            return c, True
        try:
            c.close()
        except Exception:
            pass


def _sb_release(c):
    if _SB_POOL.qsize() < _SB_POOL_MAX:
        _SB_POOL.put((c, time.time()))
    else:
        try:
            c.close()
        except Exception:
            pass


_USER_ID_RE = re.compile(r"[?&]id=eq\.([^&]+)")


def sb(method, path, body=None, headers=None):
    h = {"apikey": SRK, "Authorization": f"Bearer {SRK}",
         "Content-Type": "application/json", "Accept-Profile": "crm",
         "Content-Profile": "crm"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    url = f"{_SB_PREFIX}{path}"
    is_user_write = method != "GET" and path.startswith("/offramp_users")
    if is_user_write:   # before and after: a concurrent reader must never re-cache the pre-write row
        m = _USER_ID_RE.search(path)
        invalidate_user(urllib.parse.unquote(m.group(1)) if m else None)
    try:
        for attempt in (0, 1):
            c, reused = _sb_conn()
            try:
                c.request(method, url, body=data, headers=h)
                r = c.getresponse()
                raw = r.read()
            except Exception as ex:
                try:
                    c.close()
                except Exception:
                    pass
                if reused and attempt == 0 and isinstance(ex, _SB_STALE) and not isinstance(ex, TimeoutError):
                    continue   # the pooled socket had gone away; one retry on a fresh connection (a timeout is not stale)
                raise
            if (r.getheader("Connection") or "").lower() == "close" or r.version < 11:
                try:
                    c.close()
                except Exception:
                    pass
            else:
                _sb_release(c)
            if r.status >= 400:   # same exception type and .read() body the callers already handle
                raise urllib.error.HTTPError(f"{SB_URL}{path}", r.status, r.reason, r.headers, io.BytesIO(raw))
            txt = raw.decode()
            return json.loads(txt) if txt else []
    finally:
        if is_user_write:
            invalidate_user(urllib.parse.unquote(m.group(1)) if m else None)


SB_PROJECT_REF = _SB_HOST.split(".")[0]
SB_PAT = _read(os.path.join(SEC, "supabase-pat"))   # management-API token: read-only SQL for the state counts (same path services/perf-audit used)
SB_SQL_URL = f"https://api.supabase.com/v1/projects/{SB_PROJECT_REF}/database/query"


def sb_sql(sql, timeout=60):
    """One SQL statement through the Supabase management API. Used for read-only aggregates PostgREST cannot express
    (aggregates are disabled on this project: PGRST123)."""
    if not SB_PAT:
        raise RuntimeError("no supabase management token")
    req = urllib.request.Request(SB_SQL_URL, data=json.dumps({"query": sql}).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {SB_PAT}", "Content-Type": "application/json",
                                          "User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


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


_SAFE_NEXT = re.compile(r"^/(?![/\\])[\x21-\x7e]{0,500}$")
def _safe_next(s):
    """Same-origin relative path only (no //host, no backslash, no control chars)."""
    s = (s or "").strip()
    return s if _SAFE_NEXT.match(s) and "\\" not in s else ""


def _pw_fingerprint(u):
    return hashlib.sha256((u.get("password_hash") or "").encode()).hexdigest()[:16]


def make_reset_token(u):
    # Signed with SESSION_SECRET under its own HMAC domain ("reset:") so a reset token can never
    # be replayed as a session cookie. "ph" pins it to the current password hash: once the
    # password changes the token is dead, which makes it single-use without a DB table.
    payload = json.dumps({"k": "reset", "uid": str(u["id"]), "ph": _pw_fingerprint(u),
                          "exp": int(time.time()) + RESET_TTL}).encode()
    sig = hmac.new(SESSION_SECRET, b"reset:" + payload, hashlib.sha256).digest()
    return f"{_b64u(payload)}.{_b64u(sig)}"


def read_reset_token(token):
    try:
        p_b64, sig_b64 = token.split(".")
        payload = _b64u_dec(p_b64)
        expected = hmac.new(SESSION_SECRET, b"reset:" + payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64u_dec(sig_b64)):
            return None
        d = json.loads(payload)
        if d.get("k") != "reset" or d.get("exp", 0) < time.time():
            return None
        return d
    except Exception:
        return None


_PUBLIC_FOLDERS = {"at": 0.0, "val": frozenset()}
def public_folders():
    now = time.time()
    if now - _PUBLIC_FOLDERS["at"] > 30:
        try:
            with open(PUBLIC_FOLDERS_PATH) as f:
                names = json.load(f)
            _PUBLIC_FOLDERS["val"] = frozenset(n for n in names if isinstance(n, str))
        except Exception as ex:   # missing/broken allowlist = everything stays gated
            print(f"[gate] public_folders.json unreadable, failing closed: {ex}", flush=True)
            _PUBLIC_FOLDERS["val"] = frozenset()
        _PUBLIC_FOLDERS["at"] = now
    return _PUBLIC_FOLDERS["val"]


def gated_folder(route):
    """Return the top-level '_' folder name a request falls under when it needs the admin gate, else None."""
    try:
        # decode %5F etc, collapse //, resolve ../ -- then look at the first segment
        clean = posixpath.normpath("/" + urllib.parse.unquote(route).lstrip("/"))
    except Exception:
        return "_"
    first = clean.split("/")[1] if len(clean) > 1 else ""
    if not first.startswith("_"):
        return None
    if first in ALWAYS_GATED or first not in public_folders():
        return first
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


# perf 2026-10-03: every authenticated call re-read the user row (one PostgREST round trip, ~110-160 ms) before doing
# anything. Rows are cached per uid for USER_CACHE_TTL seconds; sb() drops the entry on any write to offramp_users
# (plan change, terms accept, password reset, usage bump, Stripe webhook), and a row fetched before the latest
# invalidation is never cached, so a concurrent reader cannot pin the pre-write row.
USER_CACHE_TTL = 60
_USER_CACHE = {}        # uid -> (expires_at, row)
_USER_INVAL = {"*": 0.0}   # uid -> last invalidation time ("*" = everyone)
_USER_LOCK = threading.Lock()


def invalidate_user(uid=None):
    now = time.time()
    with _USER_LOCK:
        if uid is None:
            _USER_CACHE.clear()
            _USER_INVAL["*"] = now
        else:
            _USER_CACHE.pop(str(uid), None)
            _USER_INVAL[str(uid)] = now


def get_user_by_id(uid, fresh=False):
    key = str(uid)
    if not fresh:
        with _USER_LOCK:
            ent = _USER_CACHE.get(key)
        if ent and ent[0] > time.time():
            return dict(ent[1])
    t0 = time.time()
    rows = sb("GET", f"/offramp_users?select=*&id=eq.{uid}&limit=1")
    u = rows[0] if rows else None
    with _USER_LOCK:
        if u and max(_USER_INVAL.get(key, 0.0), _USER_INVAL["*"]) < t0:
            _USER_CACHE[key] = (t0 + USER_CACHE_TTL, dict(u))
        else:
            _USER_CACHE.pop(key, None)
    return u


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


def record_doc_order(sess):
    """checkout.session.completed with metadata.kind=doc_pull: one row in
    crm.offramp_doc_orders (status paid), a funnel beacon and a Telegram ping so
    the PDF gets pulled and emailed within 24h. Idempotent on the session id
    (Stripe retries deliveries)."""
    md = sess.get("metadata") or {}
    sid = sess.get("id") or ""
    if sid:
        try:
            if sb("GET", f"/offramp_doc_orders?select=id&stripe_session_id=eq.{urllib.parse.quote(sid)}&limit=1"):
                return  # already recorded (webhook retry)
        except Exception as e:
            print(f"[docs] dedupe check failed: {e}", flush=True)
    doc = (md.get("doc") or "").strip().lower()
    if doc not in DOC_PULL_DOCS:
        doc = "mortgage"
    uid = md.get("user_id") or sess.get("client_reference_id") or None
    hid = md.get("hit_list_id") or None
    email = ((sess.get("customer_details") or {}).get("email") or sess.get("customer_email") or "")
    if uid and not email:
        try:
            u = get_user_by_id(uid)
            email = (u or {}).get("email") or ""
        except Exception:
            pass
    label = ""
    if hid:
        try:
            rows = sb("GET", f"/hit_list?select=property_street,property_city,property_state&id=eq.{urllib.parse.quote(str(hid))}&limit=1")
            if rows:
                r = rows[0]
                label = f"{r.get('property_street') or ''}, {r.get('property_city') or ''} {r.get('property_state') or ''}".strip(" ,")
        except Exception as e:
            print(f"[docs] hit_list lookup failed: {e}", flush=True)
    if (sess.get("payment_status") or "paid") not in ("paid", "no_payment_required"):
        notify_telegram(f"OffRamp DOC ORDER $5 NOT PAID YET ({sess.get('payment_status')}): {doc} for {label or hid} by {email} (session {sid})")
        return
    row = {"user_id": uid, "user_email": email or None, "hit_list_id": hid, "doc": doc, "status": "paid",
           "stripe_session_id": sid or None, "amount_cents": sess.get("amount_total"), "property_label": label or None}
    ins = sb("POST", "/offramp_doc_orders", body=row, headers={"Prefer": "return=representation"})
    oid = (ins[0].get("id") if isinstance(ins, list) and ins else None) or "?"
    funnel("doc_order_paid", user_id=uid, path=f"doc:{doc}")
    notify_telegram(f"OffRamp DOC ORDER $5: {DOC_PULL_DOCS[doc]} ({doc}) for {label or hid or 'unknown property'} "
                    f"by {email or 'unknown email'} — fulfil within 24h (order {oid})")


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
        "terms_accepted": bool(u.get("terms_accepted_at")),
        "plan_key": plan_key(u), "founding": bool(u.get("founding")),
        "paid": is_paid(u), "premium": has_premium(u),
        "trial": u.get("plan") == TRIAL_PLAN, "trial_ends_at": u.get("trial_ends_at"),
        "trial_eligible": trial_eligible(u), "trial_days": TRIAL_DAYS,
        "billing_portal": bool(u.get("stripe_customer_id")),
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


def _reapi_detail_cached(row):
    """Cached REAPI PropertyDetail for a hit_list row. App cache (crm.offramp_lookups) first, then the
    pipeline cache (crm.reapi_calls via crm.reapi_detail_index.reapi_property_id; expression index
    reapi_calls_data_id_idx on response->data->>id, added 2026-10-03). Returns (payload, source, fetched)."""
    addr = f"{row.get('property_street') or ''}, {row.get('property_city') or ''}, {row.get('property_state') or ''} {row.get('property_zip') or ''}".strip()
    key = norm_addr(addr)
    try:
        c = sb("GET", f"/offramp_lookups?select=payload&address_key=eq.{urllib.parse.quote(key)}&limit=1")
        if c:
            return c[0]["payload"], "cache", ""
    except Exception as e:
        print(f"[facts] app cache read failed: {e}")
    ak = row.get("addr_key")
    if ak:
        try:
            i = sb("GET", f"/reapi_detail_index?select=reapi_property_id,fetched_at&addr_key=eq.{urllib.parse.quote(ak)}&limit=1")
            if i and i[0].get("reapi_property_id"):
                # plain id match only: adding endpoint ilike + order made the planner skip the expression index (statement timeout)
                c = sb("GET", f"/reapi_calls?select=response,fetched_at&response->data->>id=eq.{urllib.parse.quote(str(i[0]['reapi_property_id']))}&limit=1")
                if c:
                    return c[0]["response"], "pipeline", (c[0].get("fetched_at") or "")
        except Exception as e:
            print(f"[facts] pipeline cache miss: {e}")
    return None, "miss", ""


def liens_from_detail(d):
    """Mortgage/lien fields from a REAPI PropertyDetail payload, mirroring services/hitlist-liens/backfill_liens.py
    (identical amounts collapse to one - Ted 2026-10-03). Returns a hit_list patch dict."""
    ms = d.get("currentMortgages") or []
    seen, liens = set(), []
    order = {"first": 1, "second": 2, "third": 3}
    for e in sorted(ms, key=lambda e: (order.get(str(e.get("position") or "").lower(), 9), e.get("seqNo") or 99)):
        try:
            amt = float(e.get("amount") or 0)
        except Exception:
            amt = 0.0
        if amt in seen:
            continue
        seen.add(amt)
        liens.append({"pos": e.get("position"), "lender": e.get("lenderName"), "amount": amt, "type": e.get("loanType"),
                      "recorded": (e.get("recordingDate") or e.get("documentDate") or "")[:10] or None})
    patch = {"lien_count": len(liens), "lien_total_amount": sum(x["amount"] for x in liens) or None,
             "lien_first_position": liens[0]["pos"] if liens else None, "lien_first_lender": liens[0]["lender"] if liens else None,
             "lien_first_amount": liens[0]["amount"] if liens else None, "lien_first_type": liens[0]["type"] if liens else None,
             "lien_second_amount": liens[1]["amount"] if len(liens) > 1 else None,
             "tax_lien": bool(d.get("taxLien")), "judgment_flag": bool(d.get("judgment")), "free_and_clear": bool(d.get("freeClear"))}
    return patch


def property_facts(row, live_ok):
    """Property facts block for the lead view (Ted 2026-10-03: more data on the property). Cached REAPI
    detail for everyone; a live PropertyDetail pull (enrich-once into offramp_lookups) only for paid users."""
    payload, src, when = _reapi_detail_cached(row)
    if payload is None:
        if not live_ok:
            return None, "locked"
        addr = f"{row.get('property_street') or ''}, {row.get('property_city') or ''}, {row.get('property_state') or ''} {row.get('property_zip') or ''}".strip()
        payload, src2 = reapi_lookup(addr)
        if payload is None:
            return None, src2
        src, when = "live", datetime.date.today().isoformat()
    d = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    if not isinstance(d, dict):
        return None, "bad-payload"
    # JIT lien fill (Ted 2026-10-03: no bulk pulls; the card completes itself one opened lead at a time)
    liens = None
    if row.get("lien_count") is None and row.get("id"):
        try:
            liens = liens_from_detail(d)
            sb("PATCH", f"/hit_list?id=eq.{urllib.parse.quote(str(row['id']))}", body=dict(liens, updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat()),
               headers={"Prefer": "return=minimal"})
        except Exception as e:
            print(f"[facts] jit lien write failed: {e}")
    pi = d.get("propertyInfo") or {}; li = d.get("lotInfo") or {}; ti = d.get("taxInfo") or {}
    ls = d.get("lastSale") or {}; oi = d.get("ownerInfo") or {}
    own_months = oi.get("ownershipLength")
    f = {
        "stories": pi.get("stories"), "garage_type": pi.get("garageType"), "garage_sqft": pi.get("garageSquareFeet"),
        "basement": pi.get("basementType") or ("Yes" if pi.get("basementSquareFeet") else None),
        "heat": pi.get("heatingType"), "cool": pi.get("airConditioningType"), "pool": pi.get("pool"),
        "fireplace": pi.get("fireplace"), "hoa": pi.get("hoa"), "beds": pi.get("bedrooms"), "baths": pi.get("bathrooms"),
        "sqft": pi.get("livingSquareFeet"), "year_built": pi.get("yearBuilt"), "property_use": pi.get("propertyUse"),
        "lot_acres": li.get("lotAcres"), "lot_sqft": li.get("lotSquareFeet"), "zoning": li.get("zoning"),
        "land_use": li.get("landUse"), "subdivision": li.get("subdivision"),
        "flood": d.get("floodZoneDescription"), "flood_zone": d.get("floodZoneType"),
        "assessed_value": ti.get("assessedValue"), "assessment_year": ti.get("assessmentYear") or ti.get("year"),
        "county_market_value": ti.get("marketValue"), "tax_amount": ti.get("taxAmount"), "tax_year": ti.get("year"),
        "tax_delinquent_year": ti.get("taxDelinquentYear"),
        "last_sale_date": ((ls.get("saleDate") or "")[:10] or None), "last_sale_amount": ls.get("saleAmount"),
        "last_sale_doc": ls.get("documentType"),
        "years_owned": (round(own_months / 12) if isinstance(own_months, (int, float)) and own_months else None),
        "owner_mailing": (oi.get("mailAddress") or {}).get("label"), "absentee": oi.get("absenteeOwner"),
        "owner_occupied": oi.get("ownerOccupied"), "corporate_owned": oi.get("corporateOwned"),
        "est_value": d.get("estimatedValue"), "fetched": (when or "")[:10], "liens": liens,
    }
    return f, src


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
SEARCH_COLS = ("addr_key,owner_absentee,surplus_amount,surplus_sale_date,surplus_purchaser,surplus_court,surplus_claim_deadline,surplus_margin,pipeline,sale_status_verified,equity_unverified,equity_verify_note,lien_count,lien_first_position,lien_first_lender,lien_first_amount,lien_first_type,lien_second_amount,lien_total_amount,tax_lien,judgment_flag,free_and_clear,sale_verified_source,verified,id,owner_full,owner_first,owner_last,property_street,property_city,"
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

# perf 2026-10-03: the feed carries only what the list card, its badges, the map pins, the search dedupe, the detail
# header and the UAT filter predicates read (plus id/photo_url). 49 columns instead of 87; phones/emails and the
# detail-only columns come down per lead through /api/property when a card is opened (app: hydrateDetail).
CARD_COLS = ("id,owner_full,owner_first,owner_last,property_street,property_city,property_state,property_zip,county,"
             "foreclosure_status,auction_date,auction_time,days_to_auction,market_value,avm,arv,"
             "equity_dollars,equity_pct,equity_unverified,mortgage_balance,mortgage_interest_rate,mortgage_recording_date,"
             "mortgage_lender,lien_count,lien_first_amount,lien_second_amount,lien_total_amount,"
             "surplus_amount,surplus_margin,sale_status_verified,sale_verified_source,trustee_opening_bid,"
             "mls_active,mls_status,deceased_flag,bankruptcy_flag,reverse_mortgage,is_judicial,property_type,next_of_kin,"
             "beds,baths,living_area_sqft,year_built,owner_absentee,mailing_address,latitude,longitude,photo_url")


# Tiered filters (Ted 2026-10-03): Free answers where/when, Pro answers how much, Premium answers who/why.
# A param above the user's tier is silently ignored and reported back in ignored_filters so the app can nudge.
FILTER_TIER = {
    "within": "free", "sale_type": "free", "ptype": "free",
    "min_equity_pct": "pro", "min_equity": "pro", "val_min": "pro", "val_max": "pro", "beds_min": "pro",
    "year_min": "pro", "absentee": "pro", "lender": "pro",
    "deceased": "premium", "bankruptcy": "premium", "reverse": "premium", "out_of_state": "premium",
    "multi": "premium", "nok": "premium", "surplus": "premium",
}
PTYPE_OR = {
    "sfr": "or=(property_type.ilike.sfr,property_type.ilike.single*)",
    "condo": "property_type=ilike.condo*",
    "mfr": "or=(property_type.ilike.mfr,property_type.ilike.multi*)",
    "mobile": "property_type=ilike.mobile*",
    "land": "property_type=ilike.land*",
}


def tier_allows(u, tier):
    if tier == "free":
        return True
    if tier == "pro":
        return u is None or is_paid(u)
    return u is None or has_premium(u)


def build_search_query(qs, limit, u=None, cols=SEARCH_COLS):
    """Returns (postgrest_path, ignored_filters). u=None means no gating (internal callers).
    cols: SEARCH_COLS (full row: export, internal) or CARD_COLS (the app feed)."""
    parts = [f"select={cols}", "active=eq.true", "auction_date=not.is.null", f"limit={limit}",  # Ted 2026-10-03: auctions only
             "order=days_to_auction.asc.nullslast"]
    # Deal room defaults to live inventory: hide auctions that already passed unless the caller
    # opts in with include_past=1. A short grace window keeps just-passed sales visible.
    if (qs.get("include_past") or [""])[0] != "1":
        parts.append("days_to_auction=gte.-3")
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

    ignored = []

    def val(name):
        v = (qs.get(name) or [""])[0].strip()
        if not v:
            return ""
        if not tier_allows(u, FILTER_TIER.get(name, "free")):
            ignored.append(name)
            return ""
        return v

    v = val("within")
    if v.isdigit():
        parts.append(f"days_to_auction=lte.{int(v)}")
    v = val("sale_type")
    if v in ("trustee", "judicial"):
        parts.append("is_judicial=eq." + ("true" if v == "judicial" else "false"))
    v = val("ptype")
    if v in PTYPE_OR:
        parts.append(PTYPE_OR[v])
    v = val("min_equity_pct")
    if v.isdigit():
        parts += [f"equity_pct=gte.{int(v)}", "equity_unverified=not.is.true"]
    v = val("min_equity")
    if v.isdigit():
        parts += [f"equity_dollars=gte.{int(v)}", "equity_unverified=not.is.true"]
    v = val("val_min")
    if v.isdigit():
        parts.append(f"avm=gte.{int(v)}")
    v = val("val_max")
    if v.isdigit():
        parts.append(f"avm=lte.{int(v)}")
    v = val("beds_min")
    if v.isdigit():
        parts.append(f"beds=gte.{int(v)}")
    v = val("year_min")
    if v.isdigit():
        parts.append(f"year_built=gte.{int(v)}")
    if val("absentee") == "1":
        parts.append("owner_absentee=is.true")
    v = val("lender")
    if v:
        parts.append(f"mortgage_lender=ilike.*{urllib.parse.quote(v)}*")
    if val("deceased") == "1":
        parts.append("or=(deceased_flag.is.true,tags.cs.{reapi-inherited})")
    if val("bankruptcy") == "1":
        parts.append("bankruptcy_flag=is.true")
    if val("reverse") == "1":
        parts.append("reverse_mortgage=is.true")
    if val("out_of_state") == "1":
        parts.append("owner_out_of_state=is.true")
    if val("multi") == "1":
        parts.append("dm_multi_property=is.true")
    if val("nok") == "1":
        parts.append("next_of_kin=not.is.null")
    if val("surplus") == "1":
        # Ted 2026-10-03: surplus = (a) sold above the debt (surplus_amount stamped by services/surplus-reconcile;
        # those rows are past-sale, so drop the live-inventory gates for them) or (b) an upcoming trustee-verified
        # sale whose value beats the credit bid by $50k+ (surplus_margin = avm - coalesce(trustee_opening_bid,
        # mortgage_balance), stored generated column). The old auction_reserve_gt_reapi_debt term was the
        # hidden-lien flag - the opposite of surplus - and is gone.
        parts = [p for p in parts if p not in ("active=eq.true", "days_to_auction=gte.-3") and not p.startswith("order=")]
        parts.append("or=(surplus_amount.not.is.null,and(active.is.true,days_to_auction.gte.-3,surplus_margin.gte.50000,"
                     "sale_verified_source.not.is.null,sale_verified_source.not.ilike.*realestateapi*,equity_unverified.not.is.true))")
        parts.append("order=surplus_amount.desc.nullslast,days_to_auction.asc.nullslast")
    return "/hit_list?" + "&".join(parts), ignored


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
    """PostgREST caps a response at 1000 rows; page through to get everything.
    perf 2026-10-03: pages are ordered by id unless the caller set an order=, so consecutive offsets never overlap
    (an unordered paged scan returned duplicate and missing rows and the state chips drifted)."""
    out = []
    sep = "&" if "?" in path else "?"
    if "order=" not in path:
        path = f"{path}{sep}order=id.asc"
        sep = "&"
    for i in range(cap):
        rows = sb("GET", f"{path}{sep}limit={page}&offset={i * page}")
        out.extend(rows)
        if len(rows) < page:
            break
    return out


_STATE_COUNTS = {"at": 0, "val": None, "refreshing": False}
_STATE_COUNTS_LOCK = threading.Lock()
# The chip must count exactly the rows a state feed shows: hit_list under the /api/search live predicate
# (active, auction_date set, days_to_auction >= -3), plus the national rows the app would ADD after its dedupe
# (UPPER(street)|zip not already a hit row; duplicate national addresses counted once). The old paging path had no
# order= so PostgREST pages overlapped and the 10-minute cache froze the drift (NV 90/96 vs 125 real, MA 104/107/79
# vs 73, CT 77 vs 100 in the 2026-10-03 UAT).
STATE_COUNTS_SQL = (
    "with live as (select upper(property_state) st, upper(coalesce(property_street,'')) street, coalesce(property_zip,'') zip "
    "  from crm.hit_list where active and auction_date is not null and days_to_auction >= -3) "
    "select 'hit' as src, st, count(*)::int as n from live where st <> '' group by st "
    "union all "
    "select 'national', upper(n.state), count(distinct (upper(coalesce(n.street,'')), coalesce(n.zip,'')))::int "
    "  from crm.offramp_national_listings n where n.status_group = 'ACTIVE' and n.delisted_at is null "
    "  and not exists (select 1 from live h where h.street = upper(coalesce(n.street,'')) and h.zip = coalesce(n.zip,'')) "
    "  group by 2")


def _state_counts_compute():
    """perf 2026-10-03: the same answer as paging 50k rows through PostgREST (35 + 16 serial calls, ~7 s cold)
    as one GROUP BY (~36 ms in Postgres). The paging path stays as the fallback if the SQL path is unavailable."""
    hit, nat = {}, {}
    try:
        for r in sb_sql(STATE_COUNTS_SQL, timeout=45):
            st = (r.get("st") or "").upper()
            if st:
                (hit if r.get("src") == "hit" else nat)[st] = int(r.get("n") or 0)
    except Exception as ex:
        print(f"[state-counts] SQL path failed ({str(ex)[:120]}); paging fallback", flush=True)
        hit, nat = {}, {}
        for r in sb_all("/hit_list?select=property_state&active=eq.true&auction_date=not.is.null&days_to_auction=gte.-3"):
            st = (r.get("property_state") or "").upper()
            if st:
                hit[st] = hit.get(st, 0) + 1
        for r in sb_all("/offramp_national_listings?select=state&status_group=eq.ACTIVE&delisted_at=is.null"):
            st = (r.get("state") or "").upper()
            if st:
                nat[st] = nat.get(st, 0) + 1
    return {"hit": hit, "national": nat, "total": {k: hit.get(k, 0) + nat.get(k, 0) for k in set(hit) | set(nat)}}


def _state_counts_refresh():
    try:
        val = _state_counts_compute()
        _STATE_COUNTS.update({"at": time.time(), "val": val})
        return val
    finally:
        _STATE_COUNTS["refreshing"] = False


def state_counts():
    """Active deal counts per state: enriched hit_list + national auction.com feed. Cached 10 min; after that the
    cached answer is served while one background thread refreshes it, so no request ever waits on the recount."""
    val = _STATE_COUNTS["val"]
    if val and time.time() - _STATE_COUNTS["at"] < 600:
        return val
    if val:
        with _STATE_COUNTS_LOCK:
            if not _STATE_COUNTS["refreshing"]:
                _STATE_COUNTS["refreshing"] = True
                threading.Thread(target=_state_counts_refresh, daemon=True).start()
        return val
    with _STATE_COUNTS_LOCK:
        _STATE_COUNTS["refreshing"] = True
    return _state_counts_refresh()   # first call after boot computes inline


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


def _ts_iso(epoch):
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
    except Exception:
        return None


def apply_subscription(u, sub, source, prev_status=None):
    """Mirror a Stripe subscription object onto the user row (webhook path).
    trialing -> plan 'trial' (Premium-level features), active -> plan from the
    price (pro/premium), canceled/unpaid/incomplete_expired -> free. Funnel
    events trial_start / paid fire on the transitions."""
    status = sub.get("status")
    price_id = (((sub.get("items") or {}).get("data") or [{}])[0].get("price") or {}).get("id")
    body = {"stripe_status": status}
    if sub.get("id"):
        body["stripe_subscription_id"] = sub["id"]
    if status == "trialing":
        body["plan"] = TRIAL_PLAN
        body["trial_ends_at"] = _ts_iso(sub.get("trial_end"))
        if not u.get("trial_used_at"):
            body["trial_used_at"] = datetime.now(timezone.utc).isoformat()
        if u.get("plan") != TRIAL_PLAN:
            funnel("trial_start", user_id=u["id"], path=f"stripe:{source}")
            notify_telegram(f"OffRamp REI: {u['email']} started the {TRIAL_DAYS}-day trial (Premium features, then Pro $49)")
    elif status == "active":
        plan, founding = plan_from_price(price_id)
        plan = plan or "pro"
        body.update({"plan": plan, "founding": founding})
        if u.get("plan") == TRIAL_PLAN or (prev_status and prev_status != "active"):
            funnel("paid", user_id=u["id"], path=f"stripe:{source}")
        if plan != u.get("plan"):
            notify_telegram(f"OffRamp REI: {u['email']} is now on {plan.title()}"
                            + (" (trial converted)" if u.get("plan") == TRIAL_PLAN else ""))
    elif status in ("canceled", "unpaid", "incomplete_expired"):
        body["plan"] = "free"
        if u.get("plan") != "free":
            notify_telegram(f"OffRamp REI: {u['email']} subscription {status}, moved to Free")
    elif status == "past_due":
        pass  # keep access; invoice.payment_failed already pinged Ted
    else:
        return u  # incomplete / paused: nothing to mirror yet
    sb("PATCH", f"/offramp_users?id=eq.{u['id']}", body=body, headers={"Prefer": "return=minimal"})
    u.update(body)
    return u


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
    for c in ("lien_first_lender", "lien_first_amount", "lien_second_amount", "lien_total_amount"):
        r[c] = None
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
    def end_headers(self):
        # The app shell and the service worker must never sit in an edge cache (Cloudflare held sw.js 4h on 2026-10-03 and phones kept a broken shell).
        pth = self.path.split("?")[0]
        if pth in ("/app/", "/app/index.html", "/app/sw.js", "/app/manifest.json"):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        elif pth.startswith("/brand/") or pth.startswith("/app/icons/"):
            self.send_header("Cache-Control", "public, max-age=86400")
        for k, v in SECURITY_HEADERS:
            self.send_header(k, v)
        super().end_headers()
    # ---- helpers ----
    def _json(self, code, obj, extra_headers=None):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if GZIP_JSON and len(payload) > GZIP_MIN and "gzip" in (self.headers.get("Accept-Encoding") or "").lower():
            payload = gzip.compress(payload, compresslevel=5)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
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

    def _gate(self, route):
        """Admin-only gate for /_<folder>/... . Returns True when the response has been sent."""
        folder = gated_folder(route)
        if not folder:
            return False
        u = self._current_user()
        if u and u.get("email") == DOC_PULL_ADMIN:
            return False
        nxt = _safe_next(route) or "/"
        if u:
            code, title, body = 403, "This page is private", (
                "<p>Your account cannot open it.</p>"
                "<a class=\"btn\" href=\"/app/\">Back to the app</a>")
        else:
            code, title, body = 401, "Sign in to view this page", (
                "<p>This page is for the account owner.</p>"
                f"<a class=\"btn\" href=\"/app/?next={urllib.parse.quote(nxt, safe='')}\">Sign in</a>")
        page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{title} - OffRamp REI</title>
<style>body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#F6F5F1;color:#15221B;font-family:-apple-system,Segoe UI,Arial,sans-serif}}
.card{{background:#fff;border-radius:18px;box-shadow:0 20px 60px rgba(0,0,0,.18);max-width:380px;width:calc(100% - 32px);padding:28px;text-align:center}}
.brand{{display:flex;align-items:center;justify-content:center;gap:8px;font-weight:800;font-size:18px;margin-bottom:14px}}.brand img{{width:28px;height:28px}}.brand span{{color:#5C6B62;font-weight:600}}
h1{{font-size:20px;margin:0 0 8px}}p{{color:#5C6B62;font-size:14px;margin:0 0 18px}}
.btn{{display:inline-block;background:#1B4332;color:#fff;text-decoration:none;font-weight:800;padding:12px 22px;border-radius:10px;font-size:15px}}</style></head>
<body><div class="card"><div class="brand"><img src="/brand/mark_ramp.png" alt="">OffRamp <span>REI</span></div><h1>{title}</h1>{body}</div></body></html>"""
        data = page.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        return True

    def do_HEAD(self):
        if self._gate(self.path.split("?")[0]):
            return
        return super().do_HEAD()

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
            if self._gate(route):   # /_<folder>/ deliverables are admin-only unless allowlisted
                return
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

        if route == "/api/docs/orders":
            # Ted-only fulfilment list: the last 100 $5 document-pull orders
            u = self._current_user()
            if not u:
                return self._json(401, {"error": "not signed in"})
            if u["email"] != DOC_PULL_ADMIN:
                return self._json(403, {"error": "forbidden"})
            try:
                rows = sb("GET", "/offramp_doc_orders?select=*&order=created_at.desc&limit=100")
            except Exception as ex:
                print(f"[docs] orders list error: {ex}", flush=True)
                return self._json(500, {"error": "orders unavailable"})
            return self._json(200, {"orders": rows, "count": len(rows),
                                    "open": sum(1 for r in rows if r.get("status") == "paid")})

        if route == "/api/auth/sso":
            provider = (qs.get("provider") or ["google"])[0]
            # Real Google OAuth when the client is configured; otherwise demo.
            if provider == "google" and GOOGLE_OAUTH_CID and GOOGLE_OAUTH_SECRET:
                host = (self.headers.get("X-Forwarded-Host")
                        or self.headers.get("Host") or "offramprei.com")
                host = host.split(",")[0].strip()
                redirect_uri = f"https://{host}/api/auth/google/callback"
                st_payload = {"n": _secrets.token_hex(8), "ru": redirect_uri}
                nxt = _safe_next((qs.get("next") or [""])[0])
                if nxt:
                    st_payload["next"] = nxt
                state = sign_state(st_payload)
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
            self.send_header("Location", _safe_next(st.get("next") or "") or "/app/")
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
            path, ignored = build_search_query(qs, limit, u, cols=CARD_COLS)
            try:
                rows = sb("GET", path)
            except urllib.error.HTTPError as e:
                return self._json(500, {"error": e.read().decode()[:200]})
            out = [gate_row(u, r) for r in rows]
            return self._json(200, {"count": len(out), "results": out, "plan": u["plan"],
                                    "paid": is_paid(u), "premium": has_premium(u), "ignored_filters": ignored})

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

        if route == "/api/property-facts":
            pid = (qs.get("id") or [""])[0]
            if not pid:
                return self._json(400, {"error": "id required"})
            rows = sb("GET", f"/hit_list?select=id,addr_key,property_street,property_city,property_state,property_zip,lien_count&id=eq.{pid}&limit=1")
            if not rows:
                return self._json(404, {"error": "not found"})
            facts, src = property_facts(rows[0], live_ok=bool(u and is_paid(u)))
            return self._json(200, {"facts": facts, "source": src})

        if route == "/api/export":
            if not is_paid(u):
                return self._json(403, {"error": "CSV export is a Pro feature", "upgrade": True})
            limit = min(limits_for(u)["export_rows"], 5000)
            rows = sb("GET", build_search_query(qs, limit, u)[0])
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

        if route == "/api/csp-report":
            # Browser CSP violation reports (report-only phase). One log line each, body capped, never fails.
            try:
                n = min(int(self.headers.get("Content-Length", 0)), 8192)
                raw = self.rfile.read(n).decode(errors="replace") if n > 0 else ""
                r = json.loads(raw or "{}")
                r = r.get("csp-report") or r   # legacy report-uri wrapper vs reporting-api shape
                if isinstance(r, list):
                    r = (r[0] or {}).get("body", {}) if r else {}
                print(f"[csp] doc={str(r.get('document-uri') or r.get('documentURL') or '')[:120]} "
                      f"blocked={str(r.get('blocked-uri') or r.get('blockedURL') or '')[:160]} "
                      f"directive={str(r.get('violated-directive') or r.get('effectiveDirective') or '')[:60]}", flush=True)
            except Exception:
                pass
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

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
                if etype == "checkout.session.completed" and (obj.get("metadata") or {}).get("kind") == "doc_pull":
                    # $5 document pull (mode=payment). Separate path so the
                    # subscription handling below never sees a one-time order.
                    record_doc_order(obj)
                    return self._json(200, {"received": True})
                if etype == "checkout.session.completed":
                    uid = obj.get("client_reference_id")
                    cust = obj.get("customer")
                    sub = obj.get("subscription")
                    u = get_user_by_id(uid) if uid else None
                    sub_obj = None
                    if u and sub and ((obj.get("metadata") or {}).get("plan") == TRIAL_PLAN
                                      or (obj.get("subscription_data") or {}).get("trial_period_days")):
                        try:
                            sub_obj = stripe_get(f"subscriptions/{sub}")
                        except Exception as e:
                            print(f"trial subscription fetch failed: {e}", flush=True)
                    if u and sub_obj and sub_obj.get("status") == "trialing":
                        if cust and u.get("stripe_customer_id") != cust:
                            sb("PATCH", f"/offramp_users?id=eq.{u['id']}", body={"stripe_customer_id": cust},
                               headers={"Prefer": "return=minimal"})
                            u["stripe_customer_id"] = cust
                        apply_subscription(u, sub_obj, "checkout.session.completed")
                        return self._json(200, {"received": True})
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
                    cust = obj.get("customer")
                    u = get_user_by_stripe_customer(cust) if cust else None
                    if u and obj.get("status") == "trialing":
                        apply_subscription(u, obj, "customer.subscription.created")
                elif etype == "customer.subscription.updated":
                    cust = obj.get("customer")
                    u = get_user_by_stripe_customer(cust) if cust else None
                    prev_status = ((event.get("data") or {}).get("previous_attributes") or {}).get("status")
                    if u and (u.get("stripe_subscription_id") in (None, "", obj.get("id"))):
                        apply_subscription(u, obj, "customer.subscription.updated", prev_status=prev_status)
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
            if plan not in PAID_PLANS and plan != TRIAL_PLAN:
                return self._json(400, {"error": "unknown plan"})
            funnel("checkout_start", user_id=u["id"], path=f"plan:{plan}", handler=self)  # cart-abandonment numerator (Ted 2026-10-03)
            if plan == TRIAL_PLAN:
                if not TRIAL_ENABLED:
                    return self._json(409, {"error": "free trial is not open yet"})
                if u.get("plan") == TRIAL_PLAN:
                    return self._json(409, {"error": "your free trial is already running"})
                if u.get("trial_used_at"):
                    return self._json(409, {"error": "you have already used your free trial - pick Pro or Premium to continue"})
                if u.get("plan") != "free":
                    return self._json(409, {"error": "you are already on a paid plan"})
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
            if plan == TRIAL_PLAN:
                # card required up front; no card by day 8 -> Stripe cancels the sub
                params["subscription_data[trial_period_days]"] = TRIAL_DAYS
                params["payment_method_collection"] = "always"
                params["subscription_data[trial_settings][end_behavior][missing_payment_method]"] = "cancel"
                params["success_url"] = "https://offramprei.com/app/?trial_started=1"
                params["metadata[plan]"] = TRIAL_PLAN
                params["subscription_data[metadata][plan]"] = "pro"
                params["subscription_data[metadata][trial]"] = "1"
                if u.get("stripe_customer_id"):  # same Stripe customer, so one trial per customer holds in Stripe too
                    params["customer"] = u["stripe_customer_id"]
                    params.pop("customer_email", None)
            try:
                sess = stripe_post("checkout/sessions", params)
            except Exception as e:
                print(f"checkout-session error (attempt 1): {e}", flush=True)
                # If we used a stored customer ID, retry without it (it may be stale/invalid)
                if params.get("customer"):
                    params2 = {k: v for k, v in params.items() if k != "customer"}
                    params2["customer_email"] = u["email"]
                    try:
                        sess = stripe_post("checkout/sessions", params2)
                    except Exception as e2:
                        print(f"checkout-session error (attempt 2): {e2}", flush=True)
                        return self._json(502, {"error": "could not start checkout"})
                else:
                    return self._json(502, {"error": "could not start checkout"})
            if not sess.get("url"):
                return self._json(502, {"error": "could not start checkout"})
            return self._json(200, {"url": sess["url"]})

        # ---------- billing: Stripe customer portal (manage card / cancel trial) ----------
        if route == "/api/billing/portal":
            u = self._current_user()
            if not u:
                return self._json(401, {"error": "not signed in"})
            if not u.get("stripe_customer_id"):
                return self._json(404, {"error": "no billing account yet"})
            try:
                ps = stripe_post("billing_portal/sessions", {"customer": u["stripe_customer_id"],
                                                             "return_url": "https://offramprei.com/app/"})
            except Exception as e:
                print(f"billing-portal error: {e}", flush=True)
                return self._json(502, {"error": "billing portal unavailable"})
            if not ps.get("url"):
                return self._json(502, {"error": "billing portal unavailable"})
            return self._json(200, {"url": ps["url"]})

        # ---------- $5 document pull: one-time Checkout for a recorded filing on a lead ----------
        if route == "/api/docs/order":
            u = self._current_user()
            if not u:
                return self._json(401, {"error": "not signed in"})
            data = self._body() or {}
            hid = str(data.get("id") or "").strip()
            doc = str(data.get("doc") or "").strip().lower()
            if doc not in DOC_PULL_DOCS:
                return self._json(400, {"error": "unknown document"})
            if not re.fullmatch(r"[0-9a-fA-F-]{36}", hid):
                return self._json(400, {"error": "bad lead id"})
            try:
                rows = sb("GET", f"/hit_list?select=id,property_street,property_city,property_state&id=eq.{urllib.parse.quote(hid)}&limit=1")
            except Exception as e:
                print(f"docs/order hit_list error: {e}", flush=True)
                rows = []
            if not rows:
                return self._json(404, {"error": "lead not found"})
            r = rows[0]
            label = f"{r.get('property_street') or ''}, {r.get('property_city') or ''} {r.get('property_state') or ''}".strip(" ,")
            funnel("doc_order_start", user_id=u["id"], path=f"doc:{doc}", handler=self)
            params = {
                "mode": "payment",
                "line_items[0][price]": DOC_PULL_PRICE,
                "line_items[0][quantity]": 1,
                "client_reference_id": u["id"],
                "metadata[kind]": "doc_pull",
                "metadata[user_id]": u["id"],
                "metadata[hit_list_id]": hid,
                "metadata[doc]": doc,
                "metadata[property_label]": label[:400],
                "payment_intent_data[description]": f"OffRamp document pull: {DOC_PULL_DOCS[doc]} - {label}"[:1000],
                "success_url": "https://offramprei.com/app/?doc_ordered=1",
                "cancel_url": "https://offramprei.com/app/",
            }
            if u.get("stripe_customer_id"):
                params["customer"] = u["stripe_customer_id"]
            else:
                params["customer_email"] = u["email"]
            try:
                sess = stripe_post("checkout/sessions", params)
            except Exception as e:
                print(f"docs/order checkout-session error: {e}", flush=True)
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
            if not data.get("terms_accepted"):  # Ted 2026-10-03: every signup reads the Terms + the state notice and checks the box
                return self._json(400, {"error": "please read the Terms and the state notice and check the box"})
            sb("POST", "/offramp_users", body={
                "email": email, "password_hash": hash_pw(pw), "full_name": name, "plan": "free",
                "terms_accepted_at": datetime.now(timezone.utc).isoformat(), "terms_version": TERMS_VERSION}, headers={"Prefer": "return=minimal"})
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

        if route == "/api/auth/forgot":
            # Always 200 so nobody can probe which emails have accounts. Same per-IP throttle as the lead forms.
            if throttled(_client_ip(self)):
                return self._json(429, {"error": "too many requests"})
            data = self._body()
            if data is None:
                return self._json(400, {"error": "bad request"})
            email = (data.get("email") or "").strip().lower()[:160]
            if not EMAIL_RE.match(email):
                return self._json(400, {"error": "valid email required"})
            def _send(em=email):
                try:
                    u = get_user_by_email(em)
                    if not u:
                        return
                    link = f"{APP_ORIGIN}/app/?reset={make_reset_token(u)}"
                    send_reset_email(em, link)
                    print(f"[reset] link sent to {em}", flush=True)
                except Exception as ex:
                    print(f"[reset] forgot error: {ex}", flush=True)
            threading.Thread(target=_send, daemon=True).start()   # off the request thread: equal timing either way
            return self._json(200, {"ok": True})

        if route == "/api/auth/reset":
            if throttled(_client_ip(self)):
                return self._json(429, {"error": "too many requests"})
            data = self._body()
            if data is None:
                return self._json(400, {"error": "bad request"})
            pw = data.get("password") or ""
            if len(pw) < 8:
                return self._json(400, {"error": "Use at least 8 characters."})
            tok = read_reset_token(str(data.get("token") or ""))
            u = get_user_by_id(urllib.parse.quote(str(tok["uid"]))) if tok else None
            if not u or not hmac.compare_digest(tok["ph"], _pw_fingerprint(u)):
                return self._json(400, {"error": "That link has expired or was already used. Request a new one."})
            sb("PATCH", f"/offramp_users?id=eq.{urllib.parse.quote(str(u['id']))}",
               body={"password_hash": hash_pw(pw), "last_login_at": datetime.now(timezone.utc).isoformat()},
               headers={"Prefer": "return=minimal"})
            print(f"[reset] password set for {u['email']}", flush=True)
            return self._json(200, {"user": public_user(ensure_period(get_user_by_id(u["id"]) or u))},
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

        # ---------- accept-terms is authenticated; exempt from the lead-capture throttle ----------
        if route == "/api/auth/accept-terms":
            tu = self._current_user()
            if not tu:
                return self._json(401, {"error": "not signed in"})
            sb("PATCH", f"/offramp_users?id=eq.{urllib.parse.quote(str(tu['id']))}",
               body={"terms_accepted_at": datetime.now(timezone.utc).isoformat(), "terms_version": TERMS_VERSION},
               headers={"Prefer": "return=minimal"})
            return self._json(200, {"ok": True})

        # ---------- public lead capture (unchanged) ----------
        if route not in ("/api/signup", "/api/unsubscribe", "/api/request-access", "/api/track",
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

        if route == "/api/track":
            # first-party funnel beacon from the app (Ted 2026-10-03: checkout abandonment). Whitelisted events only.
            ev = (data.get("event") or "").strip()
            if ev not in ("upgrade_prompt",):
                return self._json(400, {"error": "unknown event"})
            tu = self._current_user()
            funnel(ev, user_id=(tu["id"] if tu else None), path=(data.get("path") or "")[:120], handler=self)
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


def _send_email(to, subject, html, tag="email"):
    """Transactional send through the mail API (the same path the welcome email uses)."""
    if not RESEND_KEY:
        return False
    body = {"from": FROM, "to": [to], "subject": subject, "html": html}
    try:
        req = urllib.request.Request("https://api.resend.com/emails",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {RESEND_KEY}",
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30):
            return True
    except Exception as e:
        print(f"[{tag}] email deferred: {e}")
        return False


def send_reset_email(email, link):
    html = f"""<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:520px;margin:0 auto;color:#1b2b22">
<h2 style="color:#1B4332">Reset your OffRamp password</h2>
<p>Someone asked to reset the password for this account. If that was you tap the button.</p>
<p><a href="{link}" style="background:#1B4332;color:#fff;padding:11px 20px;border-radius:8px;text-decoration:none;display:inline-block">Choose a new password</a></p>
<p>The link works once. It expires in 30 minutes.</p>
<p>If this was not you ignore this email. Your password stays the same.</p>
<p>&mdash; The OffRamp REI team</p></div>"""
    return _send_email(email, "Reset your OffRamp password", html, tag="reset")


def send_welcome(first, email):
    if not RESEND_KEY or is_unsubscribed(email):
        return False
    html = f"""<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:520px;margin:0 auto;color:#1b2b22">
<h2 style="color:#1B4332">Welcome to OffRamp REI, {first}.</h2>
<p>You're in. OffRamp surfaces every upcoming auction, pre-foreclosure, and probate in your market — with verified equity and skip-traced owner contact on every lead.</p>
<p><a href="https://offramprei.com/learn" style="background:#1B4332;color:#fff;padding:11px 20px;border-radius:8px;text-decoration:none;display:inline-block">Open the Learn hub &rarr;</a></p>
<p>&mdash; The OffRamp REI team</p></div>"""
    return _send_email(email, "You're in — welcome to OffRamp REI", html, tag="signup")


if __name__ == "__main__":
    os.chdir(DIR)
    HandlerBound = functools.partial(Handler, directory=DIR)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), HandlerBound)
    print(f"OffRamp server on 127.0.0.1:{PORT} dir={DIR} "
          f"reapi={'on' if REAPI_KEY else 'off'} dm={'on' if DM_KEY else 'off'}")
    # perf 2026-10-03: warm state_counts in the background so first /api/state-counts
    # request is instant (SQL GROUP BY ~36 ms) rather than blocking boot for ~7 s.
    threading.Thread(target=lambda: state_counts(), daemon=True, name="warm-state-counts").start()
    srv.serve_forever()
