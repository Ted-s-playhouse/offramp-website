#!/usr/bin/env python3
"""OffRamp REI conversion-funnel report (stdlib only).

Reads crm.offramp_funnel_events (written by server.py funnel()) for the last
14 days and writes _funnel-report-3d7b/index.html: one row per day (America/
Denver) x [public_view, app_open, signup, trial_start, paid] with the
conversion % between consecutive steps. server.py's GET /api/funnel returns
the same JSON via summary(sb).

Counting rules
  public_view / app_open : unique visitors per day (session_hint = sha256(ip|ua)[:16])
  signup / trial_start / paid / lead : unique user_id per day (session_hint fallback)
  rows whose session_hint starts with "bot:" (crawlers, curl) are ignored
  totals row = unique over the whole window, not the sum of the daily uniques

Run:  python3 funnel_report.py          (writes the HTML, prints totals)
"""
import html, json, os, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Denver")
    TZ_NAME = "America/Denver"
except Exception:  # pragma: no cover
    TZ = timezone.utc
    TZ_NAME = "UTC"

DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DIR, "_funnel-report-3d7b", "index.html")
SB_URL = "https://scnpwjyjbcmbjzwgivlu.supabase.co/rest/v1"
SEC = os.path.expanduser("~/.cortextos/secrets")
STEPS = ["public_view", "app_open", "signup", "trial_start", "paid"]
EXTRA = ["lead", "upgrade_prompt", "checkout_start"]            # public lead form on the homepage (not an app account)
LABELS = {"public_view": "Public page views", "app_open": "App opens", "signup": "Signups",
          "trial_start": "Trial starts", "paid": "Paid", "lead": "Lead form"}
DAYS = 14
PAGE = 1000


def _sb(method, path, body=None, headers=None):
    """Standalone Supabase REST call (same contract as server.sb)."""
    key = open(os.path.join(SEC, "supabase-service-role-key")).read().strip()
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
         "Accept-Profile": "crm", "Content-Profile": "crm"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{SB_URL}{path}", data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else []


def fetch_rows(sb=_sb, days=DAYS, now=None):
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days + 1)).isoformat()
    rows, off = [], 0
    while True:
        page = sb("GET", "/offramp_funnel_events?select=at,event,user_id,session_hint,path,ref"
                         f"&at=gte.{urllib.parse.quote(since)}&order=at.asc&limit={PAGE}&offset={off}")
        rows.extend(page or [])
        off += PAGE
        if not page or len(page) < PAGE or off >= 100 * PAGE:
            return rows


def _pct(num, den):
    return round(100.0 * num / den, 1) if den else None


def _conv(counts):
    """conversion % between consecutive funnel steps: [app_open/public_view, ...]"""
    return [_pct(counts[STEPS[i + 1]], counts[STEPS[i]]) for i in range(len(STEPS) - 1)]


def summarize(rows, days=DAYS, now=None):
    now = now or datetime.now(TZ)
    today = now.astimezone(TZ).date()
    day_keys = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    uniq = {d: {s: set() for s in STEPS + EXTRA} for d in day_keys}
    raw = {d: {s: 0 for s in STEPS + EXTRA} for d in day_keys}
    window = {s: set() for s in STEPS + EXTRA}
    refs = {}
    skipped_bots = 0
    for i, r in enumerate(rows):
        ev = r.get("event")
        hint = r.get("session_hint") or ""
        if hint.startswith("bot:"):
            skipped_bots += 1
            continue
        try:
            at = datetime.fromisoformat(str(r["at"]).replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            day = at.astimezone(TZ).date().isoformat()
        except Exception:
            continue
        if day not in uniq or ev not in uniq[day]:
            continue
        if ev in ("public_view", "app_open"):
            key = hint or f"row{i}"
        else:
            key = r.get("user_id") or hint or f"row{i}"
        raw[day][ev] += 1
        uniq[day][ev].add(key)
        window[ev].add(key)
        if ev == "public_view" and r.get("ref"):
            refs[r["ref"]] = refs.get(r["ref"], 0) + 1
    out_rows = []
    for d in day_keys:
        counts = {s: len(uniq[d][s]) for s in STEPS + EXTRA}
        out_rows.append({"day": d, **counts, "raw": raw[d], "conv": _conv(counts)})
    totals = {s: len(window[s]) for s in STEPS + EXTRA}
    totals["conv"] = _conv(totals)
    # checkout abandonment (Ted 2026-10-03): users who opened a Stripe checkout and never came back paid/trialing
    co = window.get("checkout_start", set())
    done = co & (window.get("paid", set()) | window.get("trial_start", set()))
    totals["checkout_converted"] = len(done)
    totals["checkout_abandoned"] = len(co) - len(done)
    totals["checkout_abandon_pct"] = _pct(len(co) - len(done), len(co))
    totals["upgrade_prompt_to_checkout_pct"] = _pct(len(co), len(window.get("upgrade_prompt", set())))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tz": TZ_NAME, "days": days, "steps": STEPS, "extra": EXTRA,
        "rows": out_rows, "totals": totals,
        "top_referrers": sorted(refs.items(), key=lambda kv: -kv[1])[:10],
        "skipped_bot_rows": skipped_bots,
        "notes": ["public_view/app_open = unique visitors per day (ip+ua hash); "
                  "signup/trial_start/paid = unique users per day; bots excluded; "
                  "totals are unique over the whole window.",
                  "upgrade_prompt = unique users who hit a locked row/tile and saw the plan sheet; "
                  "checkout_start = unique users sent to Stripe checkout; checkout_abandon_pct = started but never paid/trialing in the window.",
                  "trial_start only fires if a Stripe subscription arrives with status 'trialing' "
                  "(trials are not built yet)."],
    }


def summary(sb=_sb, days=DAYS):
    return summarize(fetch_rows(sb, days), days)


# ------------------------------- HTML ----------------------------------------
def _fmt_pct(p):
    return "&mdash;" if p is None else f"{p:g}%"


def render_html(s):
    head = "".join(f"<th>{html.escape(LABELS[st])}</th>" + ("<th class='c'>&rarr;</th>" if i < len(STEPS) - 1 else "")
                   for i, st in enumerate(STEPS))
    head += "".join(f"<th class='x'>{html.escape(LABELS[e])}</th>" for e in EXTRA)

    def row(label, r, cls=""):
        cells = []
        for i, st in enumerate(STEPS):
            cells.append(f"<td>{r[st]}</td>")
            if i < len(STEPS) - 1:
                cells.append(f"<td class='c'>{_fmt_pct(r['conv'][i])}</td>")
        for e in EXTRA:
            cells.append(f"<td class='x'>{r[e]}</td>")
        return f"<tr class='{cls}'><td class='d'>{html.escape(label)}</td>{''.join(cells)}</tr>"

    body = "".join(row(r["day"], r) for r in reversed(s["rows"]))
    body += row(f"Last {s['days']} days (unique)", s["totals"], "tot")
    refs = "".join(f"<li>{html.escape(h)} <span>{n}</span></li>" for h, n in s["top_referrers"]) or "<li>none yet</li>"
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in s["notes"])
    gen = s["generated_at"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>OffRamp REI Funnel</title>
<style>
:root{{color-scheme:light dark;--bg:#0f1115;--card:#171a21;--fg:#e8eaf0;--mut:#8b93a7;--line:#262a35;--acc:#4f8cff}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:28px 20px}}
h1{{font-size:20px;margin:0 0 4px}} .sub{{color:var(--mut);margin:0 0 18px}}
.wrap{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}}
table{{border-collapse:collapse;width:100%;min-width:760px}}
th,td{{padding:9px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
td.d,th:first-child{{text-align:left;font-variant-numeric:tabular-nums}}
.c{{color:var(--acc);font-size:12px}} .x{{color:var(--mut)}}
tr.tot td{{font-weight:700;border-top:2px solid var(--acc)}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}}
@media(max-width:700px){{.cols{{grid-template-columns:1fr}}}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.card h2{{font-size:13px;color:var(--mut);margin:0 0 8px;text-transform:uppercase;letter-spacing:.04em}}
ul{{margin:0;padding-left:18px;color:var(--mut)}} li span{{color:var(--fg);margin-left:6px}}
code{{color:var(--acc)}}
</style></head><body><main>
<h1>OffRamp REI conversion funnel</h1>
<p class="sub">Last {s['days']} days by day ({html.escape(s['tz'])}). Generated {html.escape(gen)}. Arrows = conversion from the step on the left. JSON: <code>GET /api/funnel</code> (Ted only).</p>
<div class="wrap"><table><thead><tr><th>Day</th>{head}</tr></thead><tbody>{body}</tbody></table></div>
<div class="cols">
<div class="card"><h2>Top referrers (public views)</h2><ul>{refs}</ul></div>
<div class="card"><h2>Counting rules</h2><ul>{notes}<li>bot/curl rows skipped this window: <span>{s['skipped_bot_rows']}</span></li></ul></div>
</div>
</main></body></html>
"""


def main():
    s = summary()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(render_html(s))
    os.replace(tmp, OUT)
    t = s["totals"]
    print(f"wrote {OUT}  window={s['days']}d  " + "  ".join(f"{k}={t[k]}" for k in STEPS + EXTRA)
          + f"  conv={t['conv']}  bots_skipped={s['skipped_bot_rows']}")


if __name__ == "__main__":
    main()
