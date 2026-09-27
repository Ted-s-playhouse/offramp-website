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
import functools, urllib.request, urllib.error, urllib.parse
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
DM_KEY = _read(os.path.join(SEC, "dealmachine-skiptrace-api-key"))
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

PLAN_LIMITS = {
    "free": {"lookups": 100,  "skiptraces": 0,   "export_rows": 0},
    "pro":  {"lookups": 2000, "skiptraces": 250, "export_rows": 2000},
}
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
    lim = PLAN_LIMITS.get(u["plan"], PLAN_LIMITS["free"])
    return {
        "email": u["email"], "full_name": u.get("full_name"), "plan": u["plan"],
        "usage": {
            "lookups": {"used": int(u.get("lookups_used") or 0), "limit": lim["lookups"]},
            "skiptraces": {"used": int(u.get("skiptraces_used") or 0), "limit": lim["skiptraces"]},
        },
        "limits": lim,
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


def dm_skiptrace(address):
    if not DM_KEY:
        return None, "no-key"
    try:
        req = urllib.request.Request(
            "https://api.v2.dealmachine.com/v1/enrichment/address",
            data=json.dumps({"address": address,
                             "fields": ["full_name", "phones", "emails"],
                             "contact_audience": "owners_and_family"}).encode(),
            headers={"Authorization": f"Bearer {DM_KEY}",
                     "Content-Type": "application/json",
                     "User-Agent": "curl/8.0"}, method="POST")
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return None, f"dm {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return None, f"dm {e}"
    return data, "live"


def extract_mobiles(dm_payload):
    """Mobile/wireless numbers only, per outreach rule."""
    out = []
    seen = set()
    for block in (dm_payload or {}).get("results", []) if isinstance(dm_payload, dict) else []:
        for p in (block.get("phones") or []):
            num = re.sub(r"\D", "", str(p.get("number") or ""))
            typ = (p.get("type") or "").lower()
            if num and num not in seen and ("wireless" in typ or "mobile" in typ or "cell" in typ):
                seen.add(num)
                out.append({"number": num, "type": typ, "dnc": bool(p.get("dnc"))})
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
               "skip_traced_at,phones,emails")


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
    city = (qs.get("city") or [""])[0].strip()
    if city:
        # Match on city name OR county so a search like "Salt Lake County"
        # (a county, not a city) still finds real results instead of ~0.
        q = urllib.parse.quote(city)
        parts.append(f"or=(property_city.ilike.*{q}*,county.ilike.*{q}*)")
    status = (qs.get("status") or [""])[0].strip()
    if status:
        parts.append(f"foreclosure_status=eq.{urllib.parse.quote(status)}")
    mineq = (qs.get("min_equity") or [""])[0].strip()
    if mineq.isdigit():
        parts.append(f"equity_dollars=gte.{mineq}")
    return "/hit_list?" + "&".join(parts)


NATIONAL_COLS = ("id,listing_id,state,county,city,zip,street,address,detail_url,"
                  "primary_photo,status,status_group,auction_window,product_type,"
                  "asset_type,occupancy,trustee_sale,beds,baths,sqft,lot_size,"
                  "year_built,est_value")


def build_national_query(qs, limit):
    parts = [f"select={NATIONAL_COLS}", f"limit={limit}",
             "status_group=eq.ACTIVE", "order=id.desc"]
    state = (qs.get("state") or [""])[0].upper().strip()
    if state:
        parts.append(f"state=eq.{state}")
    city = (qs.get("city") or [""])[0].strip()
    if city:
        parts.append(f"city=ilike.*{urllib.parse.quote(city)}*")
    county = (qs.get("county") or [""])[0].strip()
    if county:
        parts.append(f"county=ilike.*{urllib.parse.quote(county)}*")
    return "/offramp_national_listings?" + "&".join(parts)


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
        "county": r.get("county"), "foreclosure_status": r.get("status"),
        "lead_status": None,
        "auction_date": r.get("auction_window"), "auction_time": None,
        "days_to_auction": None,
        "market_value": r.get("est_value"), "avm": r.get("est_value"), "arv": None,
        "equity_dollars": None, "equity_pct": None, "ltv_pct": None,
        "mortgage_balance": None,
        "beds": r.get("beds"), "baths": r.get("baths"),
        "living_area_sqft": r.get("sqft"), "year_built": r.get("year_built"),
        "occupancy": r.get("occupancy"), "property_type": r.get("structure_type"),
        "apn": None, "latitude": None, "longitude": None, "temperature": None,
        "trustee_file_no": None, "nod_case_number": None, "is_judicial": None,
        "foreclosing_attorney": None, "attorney_phone": None,
        "trustee_opening_bid": None, "auction_est_value": r.get("est_value"),
        "notice_url": r.get("detail_url"), "mailing_address": None,
        "mortgage_lender": None, "mortgage_interest_rate": None,
        "mortgage_loan_type": None, "mortgage_recording_date": None,
        "mortgage_maturity_date": None, "reverse_mortgage": None,
        "mls_active": None, "mls_status": None, "mls_list_price": None,
        "bankruptcy_flag": None, "bankruptcy_chapter": None, "deceased_flag": None,
        "skip_traced_at": None, "phones": None, "emails": None,
        "contacts_locked": True, "source": "auction.com",
        "primary_photo": r.get("primary_photo"),
    }


def strip_contacts(row):
    """Free tier sees everything EXCEPT owner phones/emails (skip-trace gated)."""
    r = dict(row)
    r["phones"] = None
    r["emails"] = None
    r["contacts_locked"] = True
    return r


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
        if not route.startswith("/api/"):
            return super().do_GET()
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if route == "/api/me":
            u = self._current_user()
            if not u:
                return self._json(401, {"error": "not signed in"})
            return self._json(200, {"user": public_user(u)})

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
            b, ct = fetch_photo(addr)
            if not b:
                return self._json(404, {"error": "no image"})
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
            locked = u["plan"] != "pro"
            out = [strip_contacts(r) if locked else r for r in rows]
            return self._json(200, {"count": len(out), "results": out, "plan": u["plan"]})

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
            r = rows[0]
            if u["plan"] != "pro":
                r = strip_contacts(r)
            r["analysis"] = analyze(r.get("arv") or r.get("avm") or r.get("market_value"),
                                    0, None, r.get("mortgage_balance"), r.get("market_value"))
            return self._json(200, {"property": r})

        if route == "/api/export":
            if u["plan"] != "pro":
                return self._json(403, {"error": "CSV export is a Pro feature", "upgrade": True})
            limit = min(PLAN_LIMITS["pro"]["export_rows"], 2000)
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
                    if u:
                        sb("PATCH", f"/offramp_users?id=eq.{u['id']}",
                           body={"plan": "pro", "stripe_customer_id": cust,
                                 "stripe_subscription_id": sub, "stripe_status": "active"},
                           headers={"Prefer": "return=minimal"})
                        notify_telegram(f"OffRamp REI: {u['email']} upgraded to Pro via Stripe checkout")
                    else:
                        notify_telegram(f"OffRamp REI Stripe checkout completed but no matching user "
                                         f"(client_reference_id={uid}, customer={cust}) — needs manual match")
                elif etype == "customer.subscription.deleted":
                    cust = obj.get("customer")
                    u = get_user_by_stripe_customer(cust) if cust else None
                    if u:
                        sb("PATCH", f"/offramp_users?id=eq.{u['id']}",
                           body={"plan": "free", "stripe_status": "canceled"},
                           headers={"Prefer": "return=minimal"})
                        notify_telegram(f"OffRamp REI: {u['email']} subscription canceled, moved to Free")
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
            cycle = (data.get("cycle") or "annual").strip().lower()
            base = STRIPE_IDS.get("link_annual_url" if cycle == "annual" else "link_monthly_url")
            if not base:
                return self._json(500, {"error": "billing not configured"})
            url = base + ("&" if "?" in base else "?") + urllib.parse.urlencode(
                {"client_reference_id": u["id"], "prefilled_email": u["email"]})
            return self._json(200, {"url": url})

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
        if route in ("/api/analyze", "/api/lookup", "/api/skiptrace"):
            u = self._current_user()
            if not u:
                return self._json(401, {"error": "not signed in"})
            data = self._body()
            if data is None:
                return self._json(400, {"error": "bad request"})

            if route == "/api/analyze":
                return self._json(200, {"analysis": analyze(
                    data.get("arv"), data.get("rehab"), data.get("offer"),
                    data.get("mortgage_balance"), data.get("market_value"))})

            if route == "/api/lookup":
                lim = PLAN_LIMITS[u["plan"]]["lookups"]
                if int(u.get("lookups_used") or 0) >= lim:
                    return self._json(403, {"error": f"monthly lookup limit reached ({lim})",
                                            "upgrade": u["plan"] == "free"})
                addr = (data.get("address") or "").strip()
                if not addr:
                    return self._json(400, {"error": "address required"})
                payload, src = reapi_lookup(addr)
                if payload is None:
                    return self._json(502, {"error": f"lookup failed ({src})"})
                if src == "live":  # only meter fresh pulls, not cache hits
                    bump(u, "lookups_used")
                return self._json(200, {"source": src, "data": payload,
                                        "usage": public_user(u)["usage"]})

            if route == "/api/skiptrace":
                if u["plan"] != "pro":
                    return self._json(403, {"error": "Skip-trace is a Pro feature", "upgrade": True})
                lim = PLAN_LIMITS["pro"]["skiptraces"]
                if int(u.get("skiptraces_used") or 0) >= lim:
                    return self._json(403, {"error": f"monthly skip-trace limit reached ({lim})"})
                addr = (data.get("address") or "").strip()
                pid = (data.get("id") or "").strip()
                if not addr and pid:
                    rows = sb("GET", f"/hit_list?select=property_street,property_city,property_state,property_zip,phones,emails&id=eq.{pid}&limit=1")
                    if rows:
                        r = rows[0]
                        # if already skip-traced in our DB, serve from there (enrich-once)
                        if r.get("phones"):
                            return self._json(200, {"source": "cache",
                                                    "mobiles": [p for p in r["phones"]
                                                                if "wireless" in (p.get("type") or "").lower()],
                                                    "emails": r.get("emails") or []})
                        addr = f"{r.get('property_street','')}, {r.get('property_city','')}, {r.get('property_state','')} {r.get('property_zip','')}"
                if not addr:
                    return self._json(400, {"error": "id or address required"})
                dm, src = dm_skiptrace(addr)
                if dm is None:
                    return self._json(502, {"error": f"skip-trace failed ({src})"})
                mobiles = extract_mobiles(dm)
                emails = []
                for block in dm.get("results", []) if isinstance(dm, dict) else []:
                    for e in (block.get("emails") or []):
                        ea = e.get("email") if isinstance(e, dict) else e
                        if ea and ea not in emails:
                            emails.append(ea)
                bump(u, "skiptraces_used")
                return self._json(200, {"source": "live", "mobiles": mobiles, "emails": emails,
                                        "usage": public_user(u)["usage"]})

        # ---------- public lead capture (unchanged) ----------
        if route not in ("/api/signup", "/api/unsubscribe", "/api/request-access"):
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
