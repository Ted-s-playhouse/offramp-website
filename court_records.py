"""court_records — CourtListener RECAP lookup for a hit_list owner (Phase 5 #35, Ted 2026-10-03).
Federal courts only: bankruptcy (Ch 7/13) + district civil. County liens/judgments are NOT here.
Free: anonymous 5 req/min; with the API token (secrets/courtlistener-api-token) 5,000/day.
Result cached on crm.hit_list.court_records for 30 days so repeat views cost nothing."""
import json, os, re, time, urllib.request, urllib.parse, urllib.error, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SEC = os.path.expanduser("~/.cortextos/secrets")
UA = "OffRampREI/1.0 (ted@americahomerestoration.com)"
CACHE_DAYS = 30
STATE_NAMES = {"AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California","CO":"Colorado","CT":"Connecticut","DE":"Delaware","DC":"District of Columbia","FL":"Florida","GA":"Georgia","HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa","KS":"Kansas","KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland","MA":"Massachusetts","MI":"Michigan","MN":"Minnesota","MS":"Mississippi","MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada","NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico","NY":"New York","NC":"North Carolina","ND":"North Dakota","OH":"Ohio","OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania","RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah","VT":"Vermont","VA":"Virginia","WA":"Washington","WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming","PR":"Puerto Rico"}

def _token():
    try:
        return open(os.path.join(SEC, "courtlistener-api-token")).read().strip()
    except Exception:
        return ""

_COURTS = None
def courts_for_state(abbr):
    """Court ids (bankruptcy + district) for a state, from cl_courts.json (fetched from
    /api/rest/v4/courts/?jurisdiction=FB|FD). Falls back to the id pattern <abbr>[nsewmc]?b."""
    global _COURTS
    if _COURTS is None:
        try:
            _COURTS = json.load(open(os.path.join(HERE, "cl_courts.json")))
        except Exception:
            _COURTS = []
    name = STATE_NAMES.get((abbr or "").upper(), "")
    ids = [c["id"] for c in _COURTS if name and re.search(r"\b" + re.escape(name) + r"$", c.get("full_name", ""))]
    if not ids and abbr:
        a = abbr.lower()
        ids = [a + m + "b" for m in ("", "n", "s", "e", "w", "m", "c")]
    return ids

def search(owner_name, state, nationwide=False):
    """One RECAP search. Returns dict {checked_at, query, count, cases[], scope} or raises."""
    name = re.sub(r"\s+", " ", (owner_name or "").replace(",", " ")).strip()
    if not name:
        return None
    # "Travis & Rachel Anderson" / "John and Jane Doe" -> search the first person: "Travis Anderson"
    m = re.match(r"^(\S+)\s+(?:&|and)\s+\S+\s+(.+)$", name, flags=re.I)
    if m:
        name = f"{m.group(1)} {m.group(2)}"
    q = f'party:("{name}")'
    params = {"type": "r", "q": q, "order_by": "dateFiled desc"}
    scope = "nationwide"
    if not nationwide:
        ids = courts_for_state(state)
        if ids:
            params["court"] = " ".join(ids); scope = (state or "").upper()
    url = "https://www.courtlistener.com/api/rest/v4/search/?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": UA}
    tok = _token()
    if tok:
        headers["Authorization"] = "Token " + tok
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=40) as r:
        j = json.loads(r.read().decode())
    cases = []
    for x in (j.get("results") or [])[:20]:
        cases.append({"case": x.get("caseName"), "court": x.get("court_citation_string") or x.get("court_id"),
                      "court_id": x.get("court_id"), "filed": x.get("dateFiled"), "terminated": x.get("dateTerminated"),
                      "docket": x.get("docketNumber"), "chapter": x.get("chapter"), "nature": x.get("suitNature"),
                      "trustee": x.get("trustee_str"),
                      "url": ("https://www.courtlistener.com" + x["docket_absolute_url"]) if x.get("docket_absolute_url") else None})
    return {"checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "query": name, "scope": scope,
            "count": j.get("count", len(cases)), "cases": cases, "source": "courtlistener"}

def is_fresh(cached):
    try:
        t = datetime.datetime.fromisoformat(cached["checked_at"].replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - t).days < CACHE_DAYS
    except Exception:
        return False
