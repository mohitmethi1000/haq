"""Deadlines, forum, fee and worker-type checks. The model never does this arithmetic."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional

from .schemas import Facts

LAKH = 100_000
CRORE = 100 * LAKH
URGENT_DAYS = 30  # deadline closer than this -> escalate to a person
COW_START = date(2025, 11, 21)  # Code on Wages, 2019 fully in force (S.O. 5322(E))
NATIONAL_HOLIDAYS = {(1, 26), (8, 15), (10, 2)}  # Republic Day, Independence Day, Gandhi Jayanti


@dataclass
class RuleResult:
    name: str
    outcome: str
    message: str
    cite: list[str] = field(default_factory=list)
    facts_used: dict = field(default_factory=dict)
    deadline: Optional[str] = None
    days_left: Optional[int] = None
    escalate: bool = False
    label: str = ""  # user-facing title, e.g. "Last date to file your wage claim"

    def dict(self) -> dict:
        return asdict(self)


def d(s: Optional[str]) -> Optional[date]:
    """Strict ISO parse (YYYY-MM-DD or YYYY-MM). Anything else -> None (shown as unknown, never guessed)."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    try:
        parts = [int(x) for x in s[:10].split("-")]
        if len(parts) == 2:
            parts.append(1)
        x = date(*parts)
    except (ValueError, TypeError):
        return None
    return x if 1950 <= x.year <= 2100 else None


def add_years(x: date, n: int) -> date:
    try:
        return x.replace(year=x.year + n)
    except ValueError:  # 29 Feb
        return x.replace(year=x.year + n, day=28)


def add_working_days(x: date, n: int) -> date:
    """Approximation (stated to the user): Sundays and national holidays are non-working; Saturdays count."""
    cur = x
    while n > 0:
        cur += timedelta(days=1)
        if cur.weekday() != 6 and (cur.month, cur.day) not in NATIONAL_HOLIDAYS:
            n -= 1
    return cur


def month_due(ym: date) -> date:
    """Monthly wages for month M are due before the expiry of the 7th of month M+1 (s.17(1)(iv))."""
    return date(ym.year + (ym.month // 12), (ym.month % 12) + 1, 7)


def months_between(a: date, b: date) -> list[date]:
    out, cur = [], date(a.year, a.month, 1)
    while cur <= date(b.year, b.month, 1) and len(out) < 120:
        out.append(cur)
        cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
    return out


def future_dates(f: Facts, today: date) -> list[str]:
    names = ["purchase_date", "refund_refused_date", "employment_end_date", "unpaid_from_month", "unpaid_to_month", "deposit_paid_date", "moved_out_date"]
    out = []
    for n in names:
        x = d(getattr(f, n))
        if x and x > today:
            out.append(f"{n}={getattr(f, n)}")
    return out


def limitation(name: str, start: Optional[date], years: int, today: date, cite: list[str], what: str,
               facts_used: dict, condonable: bool, basis: str = "") -> RuleResult:
    label = f"Last date to file your {what}"
    if not start:
        return RuleResult(name, "uncertain", f"I can't work out the time limit for your {what} without the date it started. Please add the date.",
                          cite, facts_used, label=label)
    end = add_years(start, years)
    left = (end - today).days
    if left < 0:
        extra = " The authority can still allow a late filing if you show a good reason, so talk to free legal aid (15100) before giving up." if condonable else ""
        return RuleResult(name, "out_of_time", f"The {years}-year time limit for your {what}, counted from {start:%d %b %Y}{basis}, ended on {end:%d %b %Y}.{extra}",
                          cite, facts_used, end.isoformat(), left, escalate=True, label=label)
    urgent = left <= URGENT_DAYS
    msg = f"File your {what} before {end:%d %b %Y} ({left} days left). This counts {years} years from {start:%d %b %Y}{basis}. Don't wait for the last date."
    if urgent:
        msg += " This is close - act now, and call free legal aid (15100) today."
    return RuleResult(name, "within", msg, cite, facts_used, end.isoformat(), left, escalate=urgent, label=label)


# ---------------- refunds (Consumer Protection Act, 2019) ----------------
def refund_rules(f: Facts, today: date) -> list[RuleResult]:
    out: list[RuleResult] = []
    if f.bought_for_business and f.self_employed_livelihood:
        # Whether a purchase is "exclusively for earning livelihood by self-employment" is a judgement call.
        out.append(RuleResult("consumer_status", "uncertain",
                              "The law excludes things bought for a business, but not things you use yourself only to earn your own living by self-employment. "
                              "Which one fits you is a judgement call - please check with free legal aid (15100). The steps below assume you are a consumer.",
                              ["CPA2019_s2_7"], {"self_employed_livelihood": True}, escalate=True, label="Are you a 'consumer'? Check this"))
    elif f.bought_for_business:
        out.append(RuleResult("consumer_status", "blocked",
                              "Things bought for a business usually fall outside consumer law, unless you use them only to earn your own living by self-employment. "
                              "You may still have a claim in a civil court - please talk to free legal aid (15100).",
                              ["CPA2019_s2_7"], {"bought_for_business": True}, escalate=True, label="Consumer law may not apply"))
    refused, bought = d(f.refund_refused_date), d(f.purchase_date)
    note = " Sending a letter or calling the helpline does NOT pause this clock."
    if refused:
        r = limitation("consumer_limitation", refused, 2, today, ["CPA2019_s69"], "consumer complaint",
                       {"refund_refused_date": f.refund_refused_date}, condonable=True,
                       basis=" (the date they refused; if they refused earlier, your deadline is earlier)")
    elif bought:
        safe = limitation("consumer_limitation", bought, 2, today, ["CPA2019_s69"], "consumer complaint",
                          {"purchase_date": f.purchase_date, "start_basis": "purchase date (earliest possible start, to be safe)"}, condonable=True,
                          basis=" (your purchase date - counted from the earliest possible day, to be safe)")
        if safe.outcome == "out_of_time" and add_years(bought + timedelta(days=30), 2) >= today:
            r = RuleResult("consumer_limitation", "uncertain",
                           "Two years from your purchase date have passed, but the clock may have started later (when the seller refused). I can't tell which date applies. Talk to free legal aid (15100) quickly.",
                           ["CPA2019_s69"], {"purchase_date": f.purchase_date}, escalate=True, label="Last date to file your consumer complaint")
        else:
            r = safe
    else:
        r = limitation("consumer_limitation", None, 2, today, ["CPA2019_s69"], "consumer complaint", {}, condonable=True)
    r.message += note
    out.append(r)
    if not f.bought_for_business or f.self_employed_livelihood:
        paid = f.amount_paid_inr if (f.amount_paid_inr or 0) > 0 else None
        if paid is not None:
            if paid <= 50 * LAKH:
                forum, esc = "District Consumer Commission", False
            elif paid <= 2 * CRORE:
                forum, esc = "State Consumer Commission", True
            else:
                forum, esc = "National Consumer Commission", True
            fee = "no filing fee" if paid <= 5 * LAKH else ("a Rs 200 fee" if paid <= 10 * LAKH else "a Rs 400 fee" if paid <= 20 * LAKH else "a Rs 1,000 fee" if paid <= 50 * LAKH else "a fee from the official table")
            out.append(RuleResult("forum_and_fee", "info",
                                  f"You paid Rs {paid:,.0f}, so the complaint goes to the {forum}, with {fee}. You can file in the district where you live or work, online at e-jagriti.gov.in. You don't need a lawyer to file.",
                                  ["CPJR2021_pecuniary", "CPFEE_r7", "CPA2019_s34_2", "CPA2019_s35_1"], {"amount_paid_inr": paid}, escalate=esc, label="Where to file"))
        else:
            out.append(RuleResult("forum_and_fee", "uncertain", "Tell me the price you actually paid (not the compensation you want). That decides which Consumer Commission and what fee.",
                                  ["CPJR2021_pecuniary"], {}, label="Where to file"))
        out.append(RuleResult("free_first_steps", "info",
                              "Two free steps often work first: (1) a written complaint to the seller's or platform's grievance officer, (2) the National Consumer Helpline: call 1915 or WhatsApp 8800001915.",
                              [], {}, label="Try these first"))
    return out


# ---------------- unpaid salary (Code on Wages, 2019) ----------------
NOT_EMPLOYEE = {
    "freelance_or_contract": ("blocked", "You were paid per invoice or project, so you are probably not an \"employee\" under the Code on Wages. Unpaid invoices are a contract claim (a demand letter, then a civil or commercial claim), which I don't cover. Please talk to free legal aid (15100)."),
    "gig_platform": ("uncertain", "Delivery and ride-app workers are usually treated as partners, not employees, so the wage claim route may not apply. Your app contract and the platform's grievance process matter. Please talk to free legal aid (15100)."),
    "domestic_worker": ("uncertain", "For domestic workers employed by a household, it is not certain the Code on Wages wage-claim route applies. Your State may have its own rules. Please talk to free legal aid (15100) or a domestic workers' union."),
    "government_employee": ("uncertain", "The payment-of-wages chapter of the Code on Wages does not apply to Government establishments unless the Government extends it. Government staff usually follow service rules. Please talk to free legal aid (15100)."),
}


def salary_rules(f: Facts, today: date) -> list[RuleResult]:
    out: list[RuleResult] = []
    if f.work_relationship in NOT_EMPLOYEE:
        outcome, msg = NOT_EMPLOYEE[f.work_relationship]
        out.append(RuleResult("worker_status", outcome, msg, ["COW2019_s2k"] + (["COW2019_s25"] if f.work_relationship == "government_employee" else []),
                              {"work_relationship": f.work_relationship}, escalate=True, label="Does the wage law cover you?"))
        if outcome == "blocked":
            return out

    exit_d = d(f.employment_end_date) if f.employment_end in ("resigned", "terminated") else None
    a, b = d(f.unpaid_from_month), d(f.unpaid_to_month or f.unpaid_from_month)
    dues: list[tuple[str, date]] = []
    if a and b and b >= a:
        for m in months_between(a, b):
            due = month_due(m)
            if exit_d:  # s.17(2): on exit, ALL wages payable are due within two working days
                due = min(due, add_working_days(exit_d, 2))
            dues.append((m.strftime("%B %Y"), due))
    elif exit_d:
        dues.append(("final wages", add_working_days(exit_d, 2)))

    if exit_d:
        x = add_working_days(exit_d, 2)
        out.append(RuleResult("wage_due_date", "info",
                              f"After your last day ({exit_d:%d %b %Y}), the law says final wages must be paid within two working days - about {x:%d %b %Y} "
                              f"(not counting Sundays and national holidays; your State can set a different limit).",
                              ["COW2019_s17"], {"employment_end": f.employment_end, "employment_end_date": f.employment_end_date}, x.isoformat(), label="When final pay was due"))
    elif dues:
        out.append(RuleResult("monthly_due_date", "info", f"Wages for {dues[0][0]} were due before the end of {dues[0][1]:%d %B %Y} (the 7th of the next month).",
                              ["COW2019_s17"], {"unpaid_from_month": f.unpaid_from_month}, label="When wages were due"))

    pre = [(n, x) for n, x in dues if x < COW_START]
    post = [(n, x) for n, x in dues if x >= COW_START]
    if pre:
        out.append(RuleResult("pre_code_wages", "uncertain",
                              f"Wages for {', '.join(n for n, _ in pre)} fell due before the Code on Wages started (21 Nov 2025). The older law (Payment of Wages Act) may set a different, shorter time limit for these months, which I haven't verified. Talk to free legal aid (15100) soon.",
                              ["COW2019_s69"], {"months": [n for n, _ in pre]}, escalate=True, label="Older months: check with a lawyer"))
    if post:
        res = [(n, limitation("x", x, 3, today, ["COW2019_s45"], "wage claim", {}, True)) for n, x in post]
        outs = {r.outcome for _, r in res}
        first_n, first = res[0]
        if outs == {"within"}:
            r = limitation("wage_claim_limitation", post[0][1], 3, today, ["COW2019_s45"], "wage claim",
                           {"months": [n for n, _ in post]}, True, basis=f" (when {first_n} wages fell due; each later month has its own, later deadline)")
        elif "within" in outs:
            gone = [n for n, r in res if r.outcome == "out_of_time"]
            live = [(n, r) for n, r in res if r.outcome == "within"]
            r = RuleResult("wage_claim_limitation", "partly_out_of_time",
                           f"The 3-year limit has passed for {', '.join(gone)}, but not for later months: the earliest month still in time ({live[0][0]}) must be claimed by {live[0][1].deadline}. The authority can allow late claims for good reason.",
                           ["COW2019_s45"], {"months": [n for n, _ in post]}, live[0][1].deadline, live[0][1].days_left, escalate=True, label="Last date to file your wage claim")
        else:
            r = limitation("wage_claim_limitation", post[-1][1], 3, today, ["COW2019_s45"], "wage claim", {"months": [n for n, _ in post]}, True,
                           basis=f" (when {post[-1][0]} wages fell due)")
        out.append(r)
    elif not pre:
        out.append(limitation("wage_claim_limitation", None, 3, today, ["COW2019_s45"], "wage claim", {}, True))

    out.append(RuleResult("where_to_claim", "info",
                          "You (or your union) can file a claim before the wage authority. For banks, railways, telecom, mines, insurance and central PSUs this is the Central Government's authority; for other employers it is your State labour department. "
                          "It can order up to ten times the claim as compensation and recovers unpaid amounts like land revenue. The employer has to prove they paid you.",
                          ["COW2019_s45", "COW2019_s59", "COW2019_s2d"], {}, label="Where to claim"))
    if f.monthly_wage_inr and a and b and b >= a:
        n = len(months_between(a, b))
        out.append(RuleResult("amount", "info", f"Unpaid wages: {n} month(s) x Rs {f.monthly_wage_inr:,.0f} = Rs {n * f.monthly_wage_inr:,.0f} (before any lawful deductions; HRA and overtime also count as wages for payment).",
                              ["COW2019_s2y"], {"months": n, "monthly_wage_inr": f.monthly_wage_inr, "total": n * f.monthly_wage_inr}, label="Amount"))
    if f.pf_deducted_not_deposited:
        out.append(RuleResult("pf_not_deposited", "info",
                              "If PF was cut from your salary but not deposited, the law treats the employer as having dishonestly used it (criminal breach of trust). You can also complain to EPFO.",
                              ["BNS2023_s316_expl1", "BNS2023_s316_2"], {"pf_deducted_not_deposited": True}, escalate=True, label="PF not deposited"))
    return out


# ---------------- rental deposit (state law; not verified in this corpus) ----------------
def deposit_rules(f: Facts, today: date) -> list[RuleResult]:
    return [
        RuleResult("deposit_scope", "uncertain",
                   "Deposit refunds depend on your State's rent law and your rent agreement. I haven't verified any State rent law yet, so I won't quote one. "
                   "I can still help you write a demand letter based on your facts. Free legal aid (15100) can tell you your State's rule.",
                   ["LSA1987_s12"], {"user_state": f.user_state}, escalate=True, label="Your State's law decides"),
        RuleResult("deposit_limitation", "uncertain",
                   "I haven't verified the time limit for getting a deposit back through court, so I won't guess one. Don't wait - a lawyer can confirm it quickly.",
                   [], {}, label="Time limit: not verified"),
    ]


# ---------------- cross-cutting ----------------
def common_rules(f: Facts, today: date) -> list[RuleResult]:
    out = []
    fut = future_dates(f, today)
    if fut:
        out.append(RuleResult("date_check", "uncertain", "Some dates you gave are in the future, so I've ignored them for deadlines. Please check: " + ", ".join(fut),
                              [], {"future": fut}, escalate=False, label="Check your dates"))
    if f.other_party_kind == "government":
        out.append(RuleResult("government_party", "info",
                              "The other side is a government body. If you ever need to file a civil suit in court against the Government or a public officer, the law first requires a written notice and a two-month wait. "
                              "This rule is about court suits; ask free legal aid (15100) whether it affects your route.",
                              ["CPC1908_s80"], {"other_party_kind": "government"}, escalate=True, label="Government on the other side"))
    return out


def _strip_future(f: Facts, today: date) -> Facts:
    upd = {}
    for n in ["purchase_date", "refund_refused_date", "employment_end_date", "unpaid_from_month", "unpaid_to_month", "deposit_paid_date", "moved_out_date"]:
        x = d(getattr(f, n))
        if getattr(f, n) and (x is None or x > today):
            upd[n] = None
    return f.model_copy(update=upd)


def run(f: Facts, today: date) -> list[RuleResult]:
    table = {"refund": refund_rules, "salary": salary_rules, "deposit": deposit_rules}
    clean = _strip_future(f, today)
    return common_rules(f, today) + table.get(f.dispute_type, lambda *_: [])(clean, today)
