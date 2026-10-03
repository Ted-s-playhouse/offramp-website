#!/usr/bin/env python3
"""Backfill county_fips / county_norm / county_slug on crm.offramp_national_listings
and write the reject log (crm.county_reject_log + reports/county-reject-log.md).

Idempotent. Never guesses: unmatched raw values keep their raw county and are logged.
Usage: python3 county_backfill.py [--active-only]
"""
import json, os, sys, datetime, urllib.request, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import county_norm

PAT = open(os.path.expanduser("~/.cortextos/secrets/supabase-pat")).read().strip()
URL = "https://api.supabase.com/v1/projects/scnpwjyjbcmbjzwgivlu/database/query"
T = "crm.offramp_national_listings"
DIR = os.path.dirname(os.path.abspath(__file__))


def sql(q):
    req = urllib.request.Request(URL, data=json.dumps({"query": q}).encode(),
                                 headers={"Authorization": f"Bearer {PAT}", "Content-Type": "application/json", "User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


def main():
    active_only = "--active-only" in sys.argv
    sql(f"ALTER TABLE {T} ADD COLUMN IF NOT EXISTS county_fips text, ADD COLUMN IF NOT EXISTS county_norm text, ADD COLUMN IF NOT EXISTS county_slug text, ADD COLUMN IF NOT EXISTS county_method text;"
        f"CREATE INDEX IF NOT EXISTS onl_state_slug_idx ON {T}(state, county_slug);"
        "CREATE TABLE IF NOT EXISTS crm.county_reject_log (state text, raw_county text, n_rows int, reason text, first_seen timestamptz default now(), last_seen timestamptz default now(), primary key(state, raw_county));"
        "GRANT SELECT ON crm.county_reject_log TO anon, authenticated, service_role;")
    where = "WHERE delisted_at IS NULL" if active_only else ""
    pairs = sql(f"SELECT state, county, count(*) n FROM {T} {where} GROUP BY 1,2")
    print("distinct (state,county) pairs:", len(pairs))
    ok, rejects, methods = [], [], collections.Counter()
    for p in pairs:
        r = county_norm.normalize(p["state"], p["county"])
        if r:
            ok.append((p["state"], p["county"], r)); methods[r["method"]] += 1
        else:
            rejects.append((p["state"], p["county"], p["n"]))
    print("matched:", len(ok), dict(methods), "| rejected:", len(rejects), "rows:", sum(x[2] for x in rejects))
    # apply matches via VALUES join, in chunks
    B = 300
    for i in range(0, len(ok), B):
        vals = ",".join(f"({q(st)},{q(raw)},{q(r['fips'])},{q(r['county_norm'])},{q(r['county_slug'])},{q(r['method'])})" for st, raw, r in ok[i:i+B])
        sql(f"UPDATE {T} t SET county_fips=v.fips, county_norm=v.norm, county_slug=v.slug, county_method=v.method "
            f"FROM (VALUES {vals}) AS v(state, raw, fips, norm, slug, method) WHERE t.state=v.state AND t.county=v.raw")
    # clear stale normalization on rejected pairs (in case a rule changed) and log them
    if rejects:
        vals = ",".join(f"({q(st)},{q(raw)})" for st, raw, _ in rejects)
        sql(f"UPDATE {T} t SET county_fips=NULL, county_norm=NULL, county_slug=NULL, county_method='rejected' "
            f"FROM (VALUES {vals}) AS v(state, raw) WHERE t.state=v.state AND t.county=v.raw")
        vals = ",".join(f"({q(st)},{q(raw)},{n},'no Census county match in state')" for st, raw, n in rejects)
        sql(f"INSERT INTO crm.county_reject_log(state, raw_county, n_rows, reason) VALUES {vals} "
            f"ON CONFLICT (state, raw_county) DO UPDATE SET n_rows=EXCLUDED.n_rows, last_seen=now()")
    # report
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    lines = [f"# County normalization reject log", f"Generated {now}Z — raw county values with no Census 2020 county match in their state. "
             "These rows keep their raw county label and are NOT invented into a county.", "",
             f"Pairs checked: {len(pairs)} · matched: {len(ok)} · rejected: {len(rejects)} ({sum(x[2] for x in rejects)} rows)", "",
             "| state | raw county | rows |", "|---|---|---|"]
    for st, raw, n in sorted(rejects):
        lines.append(f"| {st} | {raw} | {n} |")
    lines += ["", "## Match methods", "", "| method | pairs |", "|---|---|"] + [f"| {m} | {c} |" for m, c in methods.most_common()]
    os.makedirs(os.path.join(DIR, "reports"), exist_ok=True)
    open(os.path.join(DIR, "reports", "county-reject-log.md"), "w").write("\n".join(lines) + "\n")
    v = sql(f"SELECT count(*) FILTER (WHERE county_slug IS NOT NULL) normalized, count(*) FILTER (WHERE county_slug IS NULL) unnormalized, count(DISTINCT (state, county_slug)) distinct_counties FROM {T} WHERE delisted_at IS NULL")[0]
    print("active rows:", v)


if __name__ == "__main__":
    main()
