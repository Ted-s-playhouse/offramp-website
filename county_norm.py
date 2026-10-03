"""County normalization against the Census 2020 county FIPS list (crm.county_fips).

normalize(state, raw) -> dict(fips, county_norm, county_slug, method) or None.
Rules are applied in order; the first match wins. Nothing is guessed: a raw value
that matches no rule is returned as None and the caller writes it to the reject log.

Methods: exact | strip_suffix | parenthetical | state_suffix | city_of | split | alias
"""
import json
import os
import re
import unicodedata
import urllib.request

SB_URL = "https://scnpwjyjbcmbjzwgivlu.supabase.co/rest/v1"
SEC = os.path.expanduser("~/.cortextos/secrets")
_FIPS = None  # {state: {key: (fips, short_name, slug)}}

# Known misspellings / oddities seen in the auction.com feed (2026-10-03 survey).
# Only entries where the intended county is unambiguous. Everything else is rejected.
ALIASES = {
    ("CA", "sanbernadino"): "sanbernardino",
    ("LA", "orleansparrish"): "orleans",
    ("MI", "hillsadale"): "hillsdale",
    ("NH", "hillsbourough"): "hillsborough",
    ("NM", "donaana"): "donaana",  # Doña Ana (accent stripped by key())
    ("NY", "jerfferson"): "jefferson",
    ("NY", "naasu"): "nassau",
    ("WV", "uphsur"): "upshur",
    ("FL", "pascoes"): "pasco",
    ("MS", "districtharrison"): "harrison",
    ("MO", "cityofstlouis"): "stlouiscity",
    ("MO", "cityofsaintlouis"): "stlouiscity",
}

SUFFIX_RE = re.compile(r"\s+(county|parish|borough|census area|municipality|city and borough|municipio)$", re.I)


def _ascii(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def key(s):
    """Loose comparison key: ascii, lowercase, 'saint'->'st', letters only."""
    s = _ascii(s).lower().strip()
    s = re.sub(r"\bsaint\b", "st", s)
    s = re.sub(r"\bste\.?\b", "ste", s)
    s = s.replace("st.", "st")
    return re.sub(r"[^a-z]", "", s)


def slugify(s):
    s = _ascii(s).lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s)).strip("-")


def _load_fips():
    global _FIPS
    if _FIPS is not None:
        return _FIPS
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "county_fips.json")
    rows = None
    try:
        if os.path.exists(local):
            rows = json.load(open(local))
    except Exception:
        rows = None
    if rows is None:
        k = open(os.path.join(SEC, "supabase-service-role-key")).read().strip()
        rows = []
        off = 0
        while True:  # PostgREST caps a page at 1000 rows
            req = urllib.request.Request(
                SB_URL + f"/county_fips?select=state,fips,census_name,short_name,slug&order=fips&offset={off}&limit=1000",
                headers={"apikey": k, "Authorization": f"Bearer {k}",
                         "Accept-Profile": "crm", "User-Agent": "curl/8.0"})
            page = json.load(urllib.request.urlopen(req, timeout=60))
            rows.extend(page)
            if len(page) < 1000:
                break
            off += 1000
        try:
            os.makedirs(os.path.dirname(local), exist_ok=True)
            json.dump(rows, open(local, "w"))
        except OSError:
            pass
    out = {}
    for r in rows:
        st = out.setdefault(r["state"], {})
        # key on the short name (no 'County') AND on the full census name, so
        # 'St. Louis city' and 'Norfolk city' are reachable as 'stlouiscity'/'norfolkcity'.
        rec = (r["fips"], r["short_name"], r["slug"])
        st.setdefault(key(r["short_name"]), rec)
        st.setdefault(key(r["census_name"]), rec)
    _FIPS = out
    return out


def _hit(state, k):
    table = _load_fips().get(state) or {}
    return table.get(k)


def normalize(state, raw):
    state = (state or "").upper().strip()
    raw = (raw or "").strip()
    if not state or not raw:
        return None
    base = SUFFIX_RE.sub("", raw).strip()

    def result(rec, method):
        return {"fips": rec[0], "county_norm": rec[1], "county_slug": rec[2], "method": method}

    rec = _hit(state, key(base))
    if rec:
        return result(rec, "exact" if base == raw else "strip_suffix")
    # 'Sebastian (Fort Smith District)' -> 'Sebastian'
    m = re.sub(r"\s*\(.*?\)\s*", " ", base).strip()
    if m != base and (rec := _hit(state, key(m))):
        return result(rec, "parenthetical")
    # 'Cook-IL' -> 'Cook'
    m = re.sub(rf"[\s-]+{state}$", "", base, flags=re.I).strip()
    if m != base and (rec := _hit(state, key(m))):
        return result(rec, "state_suffix")
    # 'City of Norfolk' / 'CityofNorfolk' -> 'Norfolk city'  (VA independent cities, St. Louis city)
    m2 = re.match(r"^city\s*of\s*(.+)$", base, flags=re.I)
    if m2:
        rec = _hit(state, key(m2.group(1) + " city")) or _hit(state, key(m2.group(1)))
        if rec:
            return result(rec, "city_of")
    # 'Volusia-Daytona', 'Jackson -Kansas City', 'St. Louis_Virginia' -> first segment
    first = re.split(r"\s*[-_/]\s*", base)[0].strip()
    if first and first != base and (rec := _hit(state, key(first))):
        return result(rec, "split")
    alias = ALIASES.get((state, key(base)))
    if alias and (rec := _hit(state, alias)):
        return result(rec, "alias")
    return None


if __name__ == "__main__":
    import sys
    st, raw = sys.argv[1], " ".join(sys.argv[2:])
    print(normalize(st, raw))
