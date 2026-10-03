"""Credit-bid / trustee-sale bidding pages (Master Task File item 14).

/bid            national overview
/bid/<state>    one page per state, each written from that state's own statute with
                exact section cites. No template with swapped variables: a state only
                gets a page once its statute has actually been read. States not yet
                pulled are listed as pending, never filled in.

Sources read 2026-10-03:
  Idaho   Idaho Code § 45-1506 (legislature.idaho.gov)
  Montana Mont. Code Ann. § 71-1-315 (mca.legmt.gov)
  Utah    Utah Code § 57-1-27 (le.utah.gov)
"""
import html

ORIGIN = "https://offramprei.com"


def e(v):
    return html.escape("" if v is None else str(v), quote=True)


# Each state is hand-written. Keys: name, slug, statute, source_url, pulled, sections (list of (heading, html)).
STATES = {
    "idaho": {
        "name": "Idaho", "abbr": "ID", "slug": "idaho",
        "statute": "Idaho Code § 45-1506", "source_url": "https://legislature.idaho.gov/statutesrules/idstat/Title45/T45CH15/SECT45-1506/",
        "pulled": "2026-10-03",
        "summary": "Idaho trustee sales are non-judicial, held in the county where the property sits, and the lender may bid. The statute itself does not use the words “credit bid”, but because the beneficiary is an eligible bidder and the price is applied to the debt, the lender in practice opens at or near what it is owed.",
        "sections": [
            ("When and where the sale happens",
             "<p>The notice of sale must state “the date, time and place of the sale which shall be held at a designated time after 9:00 a.m. and before 4:00 p.m., standard time, and at a designated place in the county or one (1) of the counties where the property is located.” <cite>Idaho Code § 45-1506(4)(f)</cite>.</p>"
             "<p>Notice is mailed at least 120 days before the sale (<cite>§ 45-1506(2)</cite>) and published once a week for four successive weeks, the last publication at least 30 days before the sale (<cite>§ 45-1506(6)</cite>).</p>"),
            ("Who can bid and how the lender bids",
             "<p>“The trustee shall sell the property in one (1) parcel or in separate parcels at auction to the highest bidder. Any person, including the beneficiary under the trust deed, may bid at the trustee’s sale.” <cite>Idaho Code § 45-1506(8)</cite>.</p>"
             "<p>What this means for you: the foreclosing lender is a bidder like anyone else. Its opening bid is usually the amount owed on the loan (principal, interest, fees and costs), which is why OffRamp shows the stored loan balance as an <em>estimated</em> credit bid when no opening bid has been posted. The statute does not publish that number; the lender sets it on sale day.</p>"),
            ("Paying for the property",
             "<p>“The purchaser at the sale shall forthwith pay the price bid and upon receipt of payment the trustee shall execute and deliver the trustee’s deed to such purchaser.” If a purchaser refuses to pay, the officer making the sale may resell or reject any later bid from that person. <cite>Idaho Code § 45-1506(9)</cite>.</p>"
             "<p>Bring certified funds for the full bid. Individual trustees publish their own deposit and registration rules in the notice of sale; Idaho’s statute does not set a registration time or a deposit amount.</p>"),
            ("Postponements",
             "<p>“The trustee may postpone the sale of the property upon request of the beneficiary by publicly announcing at the time and place originally fixed for the sale the postponement to a stated subsequent date and hour. No sale may be postponed to a date more than thirty (30) days subsequent to the date from which the sale is postponed.” <cite>Idaho Code § 45-1506(8)</cite>. Rescheduled sales and the effect of a bankruptcy stay are covered in <cite>§§ 45-1506A and 45-1506B</cite>.</p>"),
        ],
    },
    "montana": {
        "name": "Montana", "abbr": "MT", "slug": "montana",
        "statute": "Mont. Code Ann. § 71-1-315", "source_url": "https://mca.legmt.gov/bills/mca/title_0710/chapter_0010/part_0030/section_0150/0710-0010-0030-0150.html",
        "pulled": "2026-10-03",
        "summary": "Montana trustee sales run under the Small Tract Financing Act. The beneficiary may bid, the trustee may not, and the winning bidder pays the full price in cash on sale day.",
        "sections": [
            ("When and where the sale happens",
             "<p>The trustee sells the property at public auction “at the date, time, and place specified in the notice of sale.” <cite>Mont. Code Ann. § 71-1-315(3)</cite>. Notice requirements are in <cite>§ 71-1-314</cite>.</p>"),
            ("Who can bid and how the lender bids",
             "<p>“Any person, including the beneficiary under the trust indenture but excluding the trustee, may bid at the sale.” <cite>Mont. Code Ann. § 71-1-315(3)</cite>.</p>"
             "<p>Montana, like Idaho, has no separate credit-bid clause. The beneficiary bids like any other participant and the proceeds are applied to the debt, so its opening number is normally the payoff. OffRamp labels the stored loan balance as an <em>estimate</em> for this reason.</p>"),
            ("Paying for the property",
             "<p>“The purchaser at the sale shall pay the price bid in cash, and upon receipt of payment, the trustee shall execute and deliver a trustee’s deed.” If the purchaser refuses to pay, the property may be resold and the refusing bidder is liable for any loss. <cite>Mont. Code Ann. § 71-1-315(4)</cite>.</p>"),
            ("Postponements",
             "<p>A sale may be postponed by public proclamation at the time and place set for the sale. Outside of a bankruptcy stay or court order, a postponement may not exceed 15 days. Where a stay or order applies, each postponement may run up to 30 days, with all postponements together capped at 120 days. No new published notice is required for a proclaimed postponement. <cite>Mont. Code Ann. § 71-1-315(3)</cite>.</p>"),
        ],
    },
    "utah": {
        "name": "Utah", "abbr": "UT", "slug": "utah",
        "statute": "Utah Code § 57-1-27", "source_url": "https://le.utah.gov/xcode/Title57/Chapter1/57-1-S27.html",
        "pulled": "2026-10-03",
        "summary": "Utah is the one of the three where the statute spells out the mechanics most bidders care about: the trustee may bid for the lender, a bid is irrevocable, the trustee may demand a deposit stated in the notice, and a defaulting bidder forfeits that deposit.",
        "sections": [
            ("When and where the sale happens",
             "<p>“On the date and at the time and place designated in the notice of sale, the trustee or the attorney for the trustee shall sell the property at public auction to the highest bidder.” The trustee or the trustee’s attorney conducts the sale and acts as auctioneer. <cite>Utah Code § 57-1-27(1)(a)–(b)</cite>. The notice of sale itself is governed by <cite>§ 57-1-25</cite>.</p>"
             "<p>If the property is several known lots or parcels, the trustor (owner) present at the sale may direct the order in which they are sold, and the trustee must follow that direction. <cite>§ 57-1-27(1)(c)–(d)</cite>.</p>"),
            ("Who can bid and how the lender bids",
             "<p>“Any person, including the beneficiary or trustee, may bid at the sale.” “The trustee may bid for the beneficiary.” “A bid is considered an irrevocable offer.” <cite>Utah Code § 57-1-27(1)(e)–(g)</cite>.</p>"
             "<p>Utah lets the trustee place the lender’s bid, which is how the credit bid is usually made in practice: the trustee opens at the amount the lender has instructed, normally the payoff. The statute does not publish that figure, so OffRamp shows the stored loan balance as an <em>estimate</em> until an opening bid is posted.</p>"),
            ("Deposits and paying for the property",
             "<p>“The trustee may, in the trustee’s discretion, require a successful bidder to make a deposit in an amount set forth in the notice of trustee’s sale.” <cite>Utah Code § 57-1-27(1)(h)</cite>. The deposit amount is therefore in the notice, not in the statute.</p>"
             "<p>If the highest bidder refuses to pay, the trustee either re-notices the sale or sells to the next highest bidder (<cite>§ 57-1-27(1)(i)</cite>). A bidder who refuses to pay is liable for the resulting loss including interest, costs and attorney fees, may have later bids rejected, forfeits the deposit, and the deposit is applied as sale proceeds under <cite>§ 57-1-29</cite>. <cite>§ 57-1-27(1)(j)</cite>. Payment timing and the trustee’s deed are in <cite>§ 57-1-28</cite>.</p>"),
            ("Postponements",
             "<p>The person conducting the sale may postpone it “for any cause that the person considers expedient,” announcing each postponement by public declaration at the time and place last set for the sale. No additional notice is required unless the postponement runs more than 45 days past the original sale date, in which case the sale must be re-noticed as if it were new. <cite>Utah Code § 57-1-27(2)(a)–(d)</cite>.</p>"),
        ],
    },
}
PENDING = ["Arizona", "Wyoming", "Nevada", "Colorado", "Washington", "Oregon", "Texas", "California"]


def shell(title, desc, body, canonical):
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}"><link rel="canonical" href="{e(canonical)}">
<style>body{{margin:0;font-family:-apple-system,system-ui,sans-serif;color:#1d2a24;background:#f7f6f2}}.hero{{background:#1B4332;color:#fff;padding:36px 20px}}.wrap{{max-width:860px;margin:0 auto;padding:0 20px}}
h1{{margin:0 0 8px;font-size:30px}}h2{{color:#1B4332;font-size:21px;margin:28px 0 8px}}p{{line-height:1.55}}cite{{font-style:normal;background:#eef3ef;border:1px solid #cfe0d6;border-radius:6px;padding:1px 6px;font-size:13px;white-space:nowrap}}
.crumbs{{font-size:13px;color:#5c6b63;margin:16px 0}}.crumbs a,.grid a{{color:#1B4332}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin:14px 0}}.card{{background:#fff;border:1px solid #e3e0d8;border-radius:12px;padding:14px}}
.card .meta{{font-size:12.5px;color:#5c6b63;margin-top:4px}}.pending{{opacity:.6}}.note{{font-size:13px;color:#5c6b63;border-top:1px solid #e3e0d8;padding-top:12px;margin-top:28px}}.cta{{display:inline-block;background:#1B4332;color:#fff;padding:11px 18px;border-radius:9px;text-decoration:none;margin:16px 0 36px}}</style></head>
<body>{body}</body></html>"""


def national_page():
    cards = []
    for st in STATES.values():
        cards.append(f'<div class="card"><a href="/bid/{st["slug"]}"><b>{e(st["name"])}</b></a><div class="meta">{e(st["statute"])} · read {e(st["pulled"])}</div></div>')
    for n in PENDING:
        cards.append(f'<div class="card pending"><b>{e(n)}</b><div class="meta">statute not yet pulled</div></div>')
    body = f"""<header class="hero"><div class="wrap"><h1>Bidding at a trustee sale</h1><p>How the lender’s credit bid works, who may bid, and how you pay. State by state, from the statute.</p></div></header>
<div class="wrap"><p class="crumbs"><a href="/">Home</a> / Bidding</p>
<h2>The national picture</h2>
<p>In the non-judicial states a trustee sells the property at public auction after the statutory notice period. The foreclosing lender (the beneficiary of the deed of trust) is almost always allowed to bid, and because any price it pays comes back to it as loan proceeds, it bids with the debt rather than with new money. That is the <b>credit bid</b>. It usually sets the floor: nobody wins the property for less than the lender is willing to take, and the lender is usually willing to take about what it is owed.</p>
<p>Three things decide whether an auction is worth your time: the lender’s likely opening number, what the property is worth, and the sale terms (where you have to be, when, and how fast you must pay). The pages below give the exact statutory rules for each state we have read. We quote the code and cite the section. If a rule is not in the statute (deposit amounts, registration times) we say so instead of guessing; those terms come from the individual notice of sale.</p>
<h2>States</h2><div class="grid">{''.join(cards)}</div>
<p class="note">Not legal advice. Each page links to the statute it was written from. Trustees publish sale-specific terms in the notice of sale; always read the notice for the property you are bidding on.</p>
<a class="cta" href="/app/">Open the deal room</a></div>"""
    return shell("Bidding at a trustee sale by state | OffRamp REI", "How lender credit bids, bidder eligibility, payment and postponement work at trustee sales, state by state with statute citations.", body, f"{ORIGIN}/bid")


def state_page(slug):
    st = STATES.get(slug)
    if not st:
        return None
    secs = "".join(f"<h2>{e(h)}</h2>{b}" for h, b in st["sections"])
    body = f"""<header class="hero"><div class="wrap"><h1>Bidding at an {e(st["name"])} trustee sale</h1><p>{e(st["summary"])}</p></div></header>
<div class="wrap"><p class="crumbs"><a href="/">Home</a> / <a href="/bid">Bidding</a> / {e(st["name"])}</p>
{secs}
<p class="note">Written from <a href="{e(st["source_url"])}" rel="noopener" target="_blank">{e(st["statute"])}</a>, read {e(st["pulled"])}. Not legal advice. Sale-specific terms come from the notice of sale.</p>
<a class="cta" href="/{e(st["slug"])}">See live {e(st["name"])} auctions</a></div>"""
    return shell(f"{st['name']} trustee sale bidding rules and credit bids | OffRamp REI",
                 f"{st['name']} trustee sale rules from {st['statute']}: who may bid, how the lender's credit bid works, payment and postponement.",
                 body, f"{ORIGIN}/bid/{slug}")
