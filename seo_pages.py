"""Public SEO pages for offramprei.com.

Reads crm.offramp_national_listings only. That table has no owner, equity,
or loan columns. Do not join hit_list. Do not scrape auction.com.
"""
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

DIR = os.path.dirname(os.path.abspath(__file__))
SB_URL = "https://scnpwjyjbcmbjzwgivlu.supabase.co/rest/v1"
SEC = os.path.expanduser("~/.cortextos/secrets")
ORIGIN = "https://offramprei.com"
SITEMAP_PATH = os.path.join(DIR, "cache", "sitemap.xml")
SITEMAP_TTL = 86400
CACHE_TTL = 600

STATES = {
    "alabama": ("AL", "Alabama"), "alaska": ("AK", "Alaska"), "arizona": ("AZ", "Arizona"),
    "arkansas": ("AR", "Arkansas"), "california": ("CA", "California"), "colorado": ("CO", "Colorado"),
    "connecticut": ("CT", "Connecticut"), "delaware": ("DE", "Delaware"), "florida": ("FL", "Florida"),
    "georgia": ("GA", "Georgia"), "hawaii": ("HI", "Hawaii"), "idaho": ("ID", "Idaho"),
    "illinois": ("IL", "Illinois"), "indiana": ("IN", "Indiana"), "iowa": ("IA", "Iowa"),
    "kansas": ("KS", "Kansas"), "kentucky": ("KY", "Kentucky"), "louisiana": ("LA", "Louisiana"),
    "maine": ("ME", "Maine"), "maryland": ("MD", "Maryland"), "massachusetts": ("MA", "Massachusetts"),
    "michigan": ("MI", "Michigan"), "minnesota": ("MN", "Minnesota"), "mississippi": ("MS", "Mississippi"),
    "missouri": ("MO", "Missouri"), "montana": ("MT", "Montana"), "nebraska": ("NE", "Nebraska"),
    "nevada": ("NV", "Nevada"), "new-hampshire": ("NH", "New Hampshire"), "new-jersey": ("NJ", "New Jersey"),
    "new-mexico": ("NM", "New Mexico"), "new-york": ("NY", "New York"), "north-carolina": ("NC", "North Carolina"),
    "north-dakota": ("ND", "North Dakota"), "ohio": ("OH", "Ohio"), "oklahoma": ("OK", "Oklahoma"),
    "oregon": ("OR", "Oregon"), "pennsylvania": ("PA", "Pennsylvania"), "rhode-island": ("RI", "Rhode Island"),
    "south-carolina": ("SC", "South Carolina"), "south-dakota": ("SD", "South Dakota"), "tennessee": ("TN", "Tennessee"),
    "texas": ("TX", "Texas"), "utah": ("UT", "Utah"), "vermont": ("VT", "Vermont"),
    "virginia": ("VA", "Virginia"), "washington": ("WA", "Washington"), "west-virginia": ("WV", "West Virginia"),
    "wisconsin": ("WI", "Wisconsin"), "wyoming": ("WY", "Wyoming"), "district-of-columbia": ("DC", "District of Columbia"),
}
ABBR = {v[0].lower(): k for k, v in STATES.items()}
NAME_TO_SLUG = {v[1].lower(): k for k, v in STATES.items()}
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
PUBLIC_COLS = ("listing_id,state,county,city,zip,street,address,auction_window,"
               "status,product_type,asset_type,occupancy,trustee_sale,beds,baths,"
               "sqft,year_built,est_value,structure_type,lot_size,county_norm,county_slug,county_fips")

_CACHE = {}


def _key():
    return open(os.path.join(SEC, "supabase-service-role-key")).read().strip()


def _get(path):
    req = urllib.request.Request(
        SB_URL + path,
        headers={"apikey": _key(), "Authorization": f"Bearer {_key()}",
                 "Accept-Profile": "crm"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode() or "[]")


def _pages(path, page=1000, cap=20):
    out = []
    for i in range(cap):
        sep = "&" if "?" in path else "?"
        rows = _get(f"{path}{sep}limit={page}&offset={i * page}")
        out.extend(rows)
        if len(rows) < page:
            break
    return out


def _cached(name, fn):
    hit = _CACHE.get(name)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    val = fn()
    _CACHE[name] = (time.time(), val)
    return val


def e(v):
    return html.escape("" if v is None else str(v), quote=True)


def slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s or "listing"


def county_slug(name):
    base = slug(name)
    if base.endswith("-county"):
        return base
    return base + "-county"


def row_county(r):
    """Display name for a row's county: Census-normalized when matched, raw feed value otherwise."""
    return r.get("county_norm") or r.get("county") or "Unknown"


def row_county_slug(r):
    return r.get("county_slug") or county_slug(r.get("county"))


def county_label(r):
    """'Salt Lake County' / 'Orleans Parish' / 'Norfolk city' — suffix comes from the Census slug."""
    name = row_county(r)
    sl = row_county_slug(r)
    if sl.endswith("-parish"):
        return f"{name} Parish"
    if sl.endswith("-borough"):
        return f"{name} Borough"
    if sl.endswith("-census-area"):
        return f"{name} Census Area"
    if sl.endswith("-municipality"):
        return f"{name} Municipality"
    if sl.endswith("-city") or name.lower().endswith(" city"):
        return name
    return f"{name} County"


def county_name_from_slug(s):
    s = s[:-7] if s.endswith("-county") else s
    return s.replace("-", " ")


def listing_slug(row):
    return slug(f"{row.get('street') or ''} {row.get('city') or ''}") or f"listing-{row.get('listing_id')}"


def human_type(v):
    raw = (v or "").replace("_", " ").strip().title()
    return raw or "Property"


def money(n):
    try:
        return f"${int(float(n)):,}"
    except (TypeError, ValueError):
        return None


def parse_window(raw):
    text = (raw or "").strip()
    if not text or text.lower() == "coming soon":
        return None
    found = []
    for mon, day, year in re.findall(r"([A-Za-z]{3,9})\s+(\d{1,2})(?:,\s*(\d{4}))?", text):
        m = MONTHS.get(mon[:3].lower())
        if not m:
            continue
        y = int(year) if year else date.today().year
        try:
            found.append(date(y, m, int(day)))
        except ValueError:
            continue
    if not found:
        return None
    return found[0], found[-1]


def window_start(row):
    parsed = parse_window(row.get("auction_window"))
    return parsed[0] if parsed else None


def overlaps_week(row, start, end):
    parsed = parse_window(row.get("auction_window"))
    if not parsed:
        return False
    a, b = parsed
    return a <= end and b >= start


def beds_baths(row):
    bits = []
    if row.get("beds") not in (None, ""):
        bits.append(f"{row['beds']} bed")
    if row.get("baths") not in (None, ""):
        bits.append(f"{row['baths']} bath")
    return ", ".join(bits)


def public_rows(state=None, county_like=None):
    parts = [f"select={PUBLIC_COLS}", "status_group=eq.ACTIVE", "delisted_at=is.null"]
    if state:
        parts.append(f"state=eq.{urllib.parse.quote(state)}")
    if county_like:
        parts.append(f"county=ilike.*{urllib.parse.quote(county_like)}*")
    return _pages("/offramp_national_listings?" + "&".join(parts))


def county_rows(abbr, slug_co):
    """All active rows for one county slug. Normalized slug first; raw-slug fallback for
    rows the normalizer rejected (they keep their raw county, see reports/county-reject-log.md)."""
    rows = [r for r in public_rows(abbr) if row_county_slug(r) == slug_co]
    return rows


def legacy_redirect(abbr, slug_st, slug_co):
    """If slug_co is an old raw-derived slug (e.g. san-bernadino-county) that now
    normalizes to a different Census slug, return the canonical path for a 301."""
    for r in public_rows(abbr):
        if r.get("county_slug") and county_slug(r.get("county")) == slug_co and r["county_slug"] != slug_co:
            return f"/{slug_st}/{r['county_slug']}"
    return None


def live_states():
    def load():
        rows = _pages("/offramp_national_listings?select=state&status_group=eq.ACTIVE&delisted_at=is.null")
        counts = {}
        for r in rows:
            st = (r.get("state") or "").upper()
            if st:
                counts[st] = counts.get(st, 0) + 1
        return counts
    return _cached("states", load)


def week_rows():
    def load():
        today = date.today()
        end = today + timedelta(days=7)
        mon = today.strftime("%b")
        rows = _pages("/offramp_national_listings?select=" + PUBLIC_COLS +
                      f"&status_group=eq.ACTIVE&delisted_at=is.null&auction_window=ilike.*{mon}*")
        hit = [r for r in rows if overlaps_week(r, today, end)]
        hit.sort(key=lambda r: window_start(r) or date.max)
        return hit[:24]
    return _cached("week", load)


def shell(title, desc, body, canonical):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(canonical)}">
<link rel="icon" href="/brand/mark_ramp.png">
<style>
  :root {{ --green:#1B4332; --green2:#2D6A4F; --mint:#b7e4c7; --paper:#F6F5F1; --ink:#15221B; --muted:#5c6b63; --line:#e3e0d8; --gold:#B8894A; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; color:var(--ink); background:var(--paper); line-height:1.55; }}
  a {{ color:var(--green2); }}
  .top {{ background:var(--green); color:#fff; }}
  .top .in, .wrap {{ max-width:1040px; margin:0 auto; padding:0 20px; }}
  .top .in {{ display:flex; align-items:center; justify-content:space-between; height:60px; }}
  .brand {{ color:#fff; font-weight:800; font-size:18px; text-decoration:none; }}
  .brand span {{ color:var(--mint); }}
  .top a.login {{ color:#e8f3ec; text-decoration:none; border:1px solid rgba(255,255,255,.28); padding:8px 14px; border-radius:999px; font-weight:700; font-size:14px; }}
  .hero {{ background:linear-gradient(160deg,var(--green),var(--green2)); color:#fff; padding:36px 0 32px; }}
  .hero h1 {{ margin:0 0 8px; font-size:34px; letter-spacing:-.02em; line-height:1.15; }}
  .hero p {{ margin:0; max-width:680px; color:#e3efe7; }}
  .wrap {{ padding-top:28px; padding-bottom:48px; }}
  .crumbs {{ font-size:13px; color:var(--muted); margin:0 0 18px; }}
  .crumbs a {{ color:var(--green2); text-decoration:none; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:12px; }}
  .card, .row {{ background:#fff; border:1px solid var(--line); border-radius:14px; padding:14px 16px; }}
  .card a, .row a.title {{ color:var(--green); font-weight:800; text-decoration:none; }}
  .meta {{ color:var(--muted); font-size:13px; margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); border-radius:14px; overflow:hidden; }}
  th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); font-size:14px; vertical-align:top; }}
  th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
  .cta {{ display:inline-block; margin-top:16px; background:var(--green); color:#fff; text-decoration:none; font-weight:800; padding:12px 16px; border-radius:10px; }}
  .note {{ font-size:13px; color:var(--muted); }}
  h2 {{ font-size:20px; margin:28px 0 12px; }}
  @media (max-width:700px) {{ .hero h1 {{ font-size:26px; }} table {{ display:block; overflow-x:auto; }} }}
</style>
</head>
<body>
<div class="top"><div class="in">
  <a class="brand" href="/">OffRamp <span>REI</span></a>
  <a class="login" href="/app/">Log in</a>
</div></div>
{body}
</body>
</html>"""


def home_block():
    week = week_rows()
    counts = live_states()
    items = []
    for r in week[:8]:
        st = (r.get("state") or "").upper()
        slug_st = ABBR.get(st.lower())
        if not slug_st:
            continue
        href = f"/{slug_st}/{county_slug(r.get('county'))}/{listing_slug(r)}"
        when = r.get("auction_window") or "Date not posted"
        city = (r.get("city") or "").title()
        items.append(
            f'<a class="seo-card" href="{e(href)}"><b>{e((r.get("street") or "").title())}</b>'
            f'<span>{e(city)}, {e(st)} · {e(when)}</span></a>')
    if not items:
        items.append('<p class="seo-empty">No auctions dated in the next 7 days in the public feed.</p>')
    links = []
    for abbr, n in sorted(counts.items(), key=lambda kv: STATES.get(ABBR.get(kv[0].lower(), ""), ("", kv[0]))[1]):
        sl = ABBR.get(abbr.lower())
        if not sl:
            continue
        name = STATES[sl][1]
        links.append(f'<a href="/{sl}">{e(name)} <em>{n:,}</em></a>')
    cards = "".join(items)
    states = "".join(links)
    return f"""<section class="seo-live"><div class="wrap">
  <h2>Auctions this week</h2>
  <p class="seo-sub">Public sale dates from the auction feed. Owner, equity, and loan figures stay off these pages.</p>
  <form class="seo-search" action="/go" method="get">
    <input name="q" placeholder="State, such as Utah or Idaho" aria-label="Search states">
    <button type="submit">Go</button>
  </form>
  <div class="seo-cards">{cards}</div>
  <h2>States</h2>
  <div class="seo-states">{states}</div>
</div></section>"""


def home_css():
    return """
<style>
  .seo-live { background:#fff; border-top:1px solid #e3e0d8; border-bottom:1px solid #e3e0d8; }
  .seo-live .wrap { max-width:1040px; margin:0 auto; padding:36px 20px 28px; }
  .seo-live h2 { margin:0 0 6px; color:#1B4332; font-size:26px; }
  .seo-sub { margin:0 0 16px; color:#5c6b63; }
  .seo-search { display:flex; gap:8px; max-width:520px; margin:0 0 18px; }
  .seo-search input { flex:1; padding:12px 14px; border:1px solid #e3e0d8; border-radius:10px; font-size:16px; }
  .seo-search button { background:#1B4332; color:#fff; border:0; border-radius:10px; padding:0 16px; font-weight:800; }
  .seo-cards, .seo-states { display:flex; flex-wrap:wrap; gap:10px; }
  .seo-card, .seo-states a { display:block; background:#F6F5F1; border:1px solid #e3e0d8; border-radius:12px; padding:12px 14px; text-decoration:none; color:#15221B; }
  .seo-card { min-width:220px; flex:1; }
  .seo-card b { display:block; }
  .seo-card span, .seo-states em { color:#5c6b63; font-style:normal; font-size:13px; }
  .seo-states a { font-weight:800; color:#1B4332; }
</style>
"""


def serve_home(handler):
    path = os.path.join(DIR, "index.html")
    raw = open(path, encoding="utf-8").read()
    block = home_css() + home_block()
    if "<!--SEO-LIVE-->" in raw:
        raw = raw.replace("<!--SEO-LIVE-->", block, 1)
    else:
        raw = raw.replace("</header>", "</header>\n" + block, 1)
    _send(handler, 200, raw, "text/html; charset=utf-8")


def state_page(slug_st):
    abbr, name = STATES[slug_st]
    rows = public_rows(abbr)
    today = date.today()
    soon = [r for r in rows if (window_start(r) or date.max) >= today]
    soon.sort(key=lambda r: window_start(r) or date.max)
    counties = {}
    for r in rows:
        sl = row_county_slug(r)
        if sl not in counties:
            counties[sl] = {"label": county_label(r), "n": 0}
        counties[sl]["n"] += 1
    county_cards = []
    for sl, c in sorted(counties.items(), key=lambda kv: (-kv[1]["n"], kv[1]["label"])):
        county_cards.append(
            f'<div class="card"><a href="/{slug_st}/{sl}">{e(c["label"])}</a>'
            f'<div class="meta">{c["n"]:,} public listings</div></div>')
    auction_rows = []
    for r in soon[:40]:
        auction_rows.append(
            f'<tr><td><a href="/{slug_st}/{row_county_slug(r)}/{listing_slug(r)}">{e((r.get("street") or "").title())}</a>'
            f'<div class="meta">{e((r.get("city") or "").title())}</div></td>'
            f'<td>{e(row_county(r))}</td><td>{e(r.get("auction_window") or "Date not posted")}</td></tr>')
    body = f"""<header class="hero"><div class="wrap"><h1>{e(name)} foreclosure auctions</h1>
<p>Counties and upcoming public sales in {e(name)}.</p></div></header>
<div class="wrap">
<p class="crumbs"><a href="/">Home</a> / {e(name)}</p>
<h2>Counties</h2>
<div class="grid">{''.join(county_cards) or '<p>No public listings yet.</p>'}</div>
<h2>Upcoming auctions</h2>
<table><thead><tr><th>Address</th><th>County</th><th>Sale date</th></tr></thead>
<tbody>{''.join(auction_rows) or '<tr><td colspan="3">No upcoming dates in the public feed.</td></tr>'}</tbody></table>
<a class="cta" href="/app/">Open the deal room</a>
</div>"""
    title = f"{name} foreclosure auctions | OffRamp REI"
    desc = f"Upcoming public foreclosure and auction listings in {name}, listed by county. Sale dates from the public auction feed."
    return shell(title, desc, body, f"{ORIGIN}/{slug_st}")


def county_page(slug_st, slug_co):
    abbr, name = STATES[slug_st]
    rows = county_rows(abbr, slug_co)
    if not rows:
        return None
    county = row_county(rows[0])
    label = county_label(rows[0])
    today = date.today()
    rows.sort(key=lambda r: window_start(r) or date.max)
    body_rows = []
    for r in rows:
        if window_start(r) and window_start(r) < today - timedelta(days=14):
            continue
        body_rows.append(
            f'<tr><td><a href="/{slug_st}/{slug_co}/{listing_slug(r)}">{e((r.get("street") or "").title())}</a>'
            f'<div class="meta">{e((r.get("city") or "").title())} {e(r.get("zip") or "")}</div></td>'
            f'<td>{e(r.get("auction_window") or "Date not posted")}</td>'
            f'<td>{e(human_type(r.get("structure_type")))}</td></tr>')
    shown = "".join(body_rows) or '<tr><td colspan="3">No current public sales in this county.</td></tr>'
    body = f"""<header class="hero"><div class="wrap"><h1>{e(label)}, {e(name)} auctions</h1>
<p>Public listings with sale dates.</p></div></header>
<div class="wrap">
<p class="crumbs"><a href="/">Home</a> / <a href="/{slug_st}">{e(name)}</a> / {e(label)}</p>
<table><thead><tr><th>Property</th><th>Sale date</th><th>Type</th></tr></thead>
<tbody>{shown}</tbody></table>
<p class="note">{len(rows):,} public listings in this county. Private underwriting is not on this page.</p>
<a class="cta" href="/app/">Open the deal room</a>
</div>"""
    title = f"{label}, {name} foreclosure auctions | OffRamp REI"
    desc = f"Public auction listings in {label}, {name}, with sale dates and property type."
    return shell(title, desc, body, f"{ORIGIN}/{slug_st}/{slug_co}")


def listing_page(slug_st, slug_co, slug_li):
    abbr, name = STATES[slug_st]
    rows = [r for r in county_rows(abbr, slug_co) if listing_slug(r) == slug_li]
    if not rows:
        return None
    r = rows[0]
    street = (r.get("street") or "This property").title()
    city = (r.get("city") or "").title()
    county = row_county(r)
    when = r.get("auction_window") or "not posted"
    kind = human_type(r.get("structure_type") or r.get("asset_type"))
    bb = beds_baths(r)
    value = money(r.get("est_value")) or "not posted"
    sqft = f"{int(r['sqft']):,} sq ft" if str(r.get("sqft") or "").replace(".", "", 1).isdigit() else None
    year = r.get("year_built")
    occ = (r.get("occupancy") or "").replace("_", " ").title()
    sale_kind = "a trustee sale" if r.get("trustee_sale") else "a public auction listing"
    facts = ", ".join(x for x in [bb, sqft, f"built {year}" if year else None] if x)
    p1 = (f"{street} in {city}, {county} County, {name} is {sale_kind} with a sale window of {when}. "
          f"The public record lists it as a {kind.lower()}"
          + (f", {facts}." if facts else "."))
    p2 = (f"Estimated value in the public feed is {value}. "
          f"{('Occupancy is listed as ' + occ + '. ') if occ else ''}"
          f"This page is the public listing only. Underwriting lives in the deal room.")
    body = f"""<header class="hero"><div class="wrap"><h1>{e(street)}</h1>
<p>{e(city)}, {e(county)} County, {e(name)} {e(r.get('zip') or '')}</p></div></header>
<div class="wrap">
<p class="crumbs"><a href="/">Home</a> / <a href="/{slug_st}">{e(name)}</a> / <a href="/{slug_st}/{slug_co}">{e(county)} County</a> / {e(street)}</p>
<table><tbody>
<tr><th>Address</th><td>{e(street)}, {e(city)}, {e(abbr)} {e(r.get('zip') or '')}</td></tr>
<tr><th>County</th><td>{e(county)}</td></tr>
<tr><th>Sale date</th><td>{e(when)}</td></tr>
<tr><th>Property type</th><td>{e(kind)}</td></tr>
<tr><th>Beds / baths</th><td>{e(bb or 'Not posted')}</td></tr>
<tr><th>Estimated value</th><td>{e(value)}</td></tr>
</tbody></table>
<h2>About this sale</h2>
<p>{e(p1)}</p>
<p>{e(p2)}</p>
<a class="cta" href="/app/">Open the deal room</a>
</div>"""
    title = f"{street}, {city}, {name} auction | OffRamp REI"
    desc = f"Public auction listing for {street}, {city}, {county} County, {name}. Sale window {when}."
    return shell(title, desc, body, f"{ORIGIN}/{slug_st}/{slug_co}/{slug_li}")


def sitemap_xml():
    try:
        if os.path.exists(SITEMAP_PATH) and time.time() - os.path.getmtime(SITEMAP_PATH) < SITEMAP_TTL:
            return open(SITEMAP_PATH, encoding="utf-8").read()
    except OSError:
        pass
    rows = _pages("/offramp_national_listings?select=listing_id,state,county,county_slug,city,street,updated_at&status_group=eq.ACTIVE&delisted_at=is.null", cap=30)
    urls = [f"{ORIGIN}/"]
    seen_states = set()
    seen_counties = set()
    for r in rows:
        st = (r.get("state") or "").upper()
        sl = ABBR.get(st.lower())
        if not sl:
            continue
        if sl not in seen_states:
            seen_states.add(sl)
            urls.append(f"{ORIGIN}/{sl}")
        co = f"{ORIGIN}/{sl}/{row_county_slug(r)}"
        if co not in seen_counties:
            seen_counties.add(co)
            urls.append(co)
        urls.append(f"{ORIGIN}/{sl}/{row_county_slug(r)}/{listing_slug(r)}")
    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        body.append(f"<url><loc>{e(u)}</loc></url>")
    body.append("</urlset>")
    xml = "\n".join(body)
    try:
        os.makedirs(os.path.dirname(SITEMAP_PATH), exist_ok=True)
        open(SITEMAP_PATH, "w", encoding="utf-8").write(xml)
    except OSError:
        pass
    return xml


def go(qs):
    q = (qs.get("q") or [""])[0].strip().lower()
    if q in STATES:
        return f"/{q}"
    if q in ABBR:
        return f"/{ABBR[q]}"
    if q in NAME_TO_SLUG:
        return f"/{NAME_TO_SLUG[q]}"
    q2 = q.replace(" ", "-")
    if q2 in STATES:
        return f"/{q2}"
    return "/"


def _send(handler, code, body, ctype):
    data = body.encode("utf-8") if isinstance(body, str) else body
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def serve(handler, route, qs):
    path = route.rstrip("/") or "/"
    if path == "/sitemap.xml":
        try:
            _send(handler, 200, sitemap_xml(), "application/xml")
        except Exception as ex:
            _send(handler, 503, f"sitemap unavailable: {ex}", "text/plain")
        return True
    if path == "/go":
        handler.send_response(302)
        handler.send_header("Location", go(qs))
        handler.end_headers()
        return True
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] not in STATES:
        return False
    try:
        if len(parts) == 1:
            page = state_page(parts[0])
        elif len(parts) == 2:
            page = county_page(parts[0], parts[1])
            if not page:
                target = legacy_redirect(STATES[parts[0]][0], parts[0], parts[1])
                if target:
                    handler.send_response(301)
                    handler.send_header("Location", target)
                    handler.end_headers()
                    return True
        elif len(parts) == 3:
            page = listing_page(parts[0], parts[1], parts[2])
        else:
            return False
    except Exception as ex:
        _send(handler, 503, f"Page unavailable: {html.escape(str(ex))}", "text/html")
        return True
    if not page:
        return False
    _send(handler, 200, page, "text/html; charset=utf-8")
    return True
