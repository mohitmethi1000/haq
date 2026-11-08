"""Demand letters filled by code from confirmed facts. Unknowns become [FILL: ...] slots; the user edits, signs and sends."""
from __future__ import annotations

from datetime import date

from . import corpus
from .rules import RuleResult, d
from .schemas import Facts


def _f(v, label: str) -> str:
    if v in (None, "", []) or (isinstance(v, (int, float)) and v <= 0):
        return f"[FILL: {label}]"
    if isinstance(v, (int, float)):
        return f"Rs {v:,.0f}"
    return str(v)


def _date(s, label: str) -> str:
    x = d(s)
    return x.strftime("%d %B %Y") if x else f"[FILL: {label}]"


def _month(s, label: str) -> str:
    x = d(s)
    return x.strftime("%B %Y") if x else f"[FILL: {label}]"


def _rule(results: list[RuleResult], name: str):
    return next((r for r in results if r.name == name), None)


def _grounds(results: list[RuleResult]) -> list[str]:
    secs = corpus.by_id()
    ids = []
    for r in results:
        for c in r.cite:
            if c not in ids and c in secs and secs[c].get("kind", "").startswith("verbatim"):
                ids.append(c)
    return [f"{secs[c]['act']}, {secs[c]['section']} ({secs[c]['heading']})" for c in ids]


def demand_letter(f: Facts, results: list[RuleResult], today: date, user_name: str | None = None) -> str:
    head = [
        f"Date: {today:%d %B %Y}",
        f"From: {_f(user_name, 'your full name')}, [FILL: your full postal address]",
        f"To: {_f(f.other_party_name, 'name of the other party')}, [FILL: their address / registered office / email]",
        "",
    ]
    if f.dispute_type == "salary":
        amt_rule = _rule(results, "amount")
        total = amt_rule.facts_used.get("total") if amt_rule else None
        amount = _f(total or f.amount_claimed_inr, "total unpaid amount")
        a, b = f.unpaid_from_month, f.unpaid_to_month or f.unpaid_from_month
        period = _month(a, "unpaid month") if (a == b) else f"{_month(a, 'first unpaid month')} to {_month(b, 'last unpaid month')}"
        exit_line = ""
        if f.employment_end in ("resigned", "terminated"):
            exit_line = f" My last working day was {_date(f.employment_end_date, 'last working day')}."
        body = [
            "Subject: Demand for payment of unpaid wages",
            "",
            f"I was employed with you{' at a monthly wage of ' + _f(f.monthly_wage_inr, 'monthly salary') if f.monthly_wage_inr else ''}. "
            f"My wages for {period} have not been paid.{exit_line}",
            "",
            f"I request you to pay the unpaid amount of {amount} within 15 days of receiving this letter, to my bank account [FILL: account details].",
            "",
            "If the amount is not paid, I intend to file a claim before the authority under the Code on Wages, 2019, which may also award compensation, without further notice to you.",
        ]
    elif f.dispute_type == "refund":
        fr = _rule(results, "forum_and_fee")
        forum = "the Consumer Commission"
        if fr and fr.outcome == "info":
            for name in ("District Consumer Commission", "State Consumer Commission", "National Consumer Commission"):
                if name in fr.message:
                    forum = "the " + name
        body = [
            "Subject: Demand for refund",
            "",
            f"On {_date(f.purchase_date, 'date of purchase')} I paid {_f(f.amount_paid_inr, 'amount paid')} to you for [FILL: product/service and order number]. "
            "[FILL: what went wrong, e.g. product defective / service not provided]. Despite my requests"
            + (f", including on {_date(f.refund_refused_date, 'date')}," if f.refund_refused_date else "")
            + " you have not refunded the amount.",
            "",
            f"I request a full refund of {_f(f.amount_claimed_inr or f.amount_paid_inr, 'amount')} within 15 days of receiving this letter.",
            "",
            f"If the refund is not made, I intend to file a complaint before {forum} under the Consumer Protection Act, 2019, without further notice to you.",
        ]
    elif f.dispute_type == "deposit":
        moved = (f"I vacated the premises and handed over the keys on {_date(f.moved_out_date, 'date you moved out')}."
                 if f.moved_out_date else "[FILL: the date you vacated and handed over the keys - only if you have moved out]")
        body = [
            "Subject: Demand for return of security deposit",
            "",
            f"I paid you a security deposit of {_f(f.amount_claimed_inr, 'deposit amount')} on {_date(f.deposit_paid_date, 'date paid')} for the premises at [FILL: address]. "
            f"{moved} The deposit has not been returned.",
            "",
            "I request you to return the deposit, less any deduction you can justify in writing with bills, within 15 days of receiving this letter.",
            "",
            "If it is not returned, I will take legal action to recover it, without further notice to you.",
        ]
    else:
        return ""
    evid = ["", "I have the following records: " + (", ".join(f.evidence) if f.evidence else "[FILL: receipts, chats, emails, bank statements]") + "."]
    tail = ["", "Yours faithfully,", _f(user_name, "your name and signature")]
    return "\n".join(head + body + evid + tail)


def sending_notes(f: Facts, results: list[RuleResult]) -> str:
    grounds = _grounds(results)
    where = {"salary": "your offer letter, salary slip, or the company's registered office on the MCA website (mca.gov.in)",
             "refund": "the invoice, order page, or the seller's 'grievance officer' details on the website/app",
             "deposit": "your rent agreement"}.get(f.dispute_type, "your documents")
    return "\n".join([
        "How to send it:",
        f"1. Find their correct address in {where}.",
        "2. Fill every [FILL] slot. Remove anything that isn't true - only state facts you can prove.",
        "3. Send by Speed Post and keep the receipt and the tracking proof of delivery. Also send a copy by email or WhatsApp and keep a screenshot.",
        "4. Wait 15 days. Sending this letter does NOT stop any legal time limit - file your claim/complaint before the deadline shown, even if they haven't replied.",
        "5. Free help to check the letter: legal aid 15100 or Tele-Law 14454.",
        "",
        "Laws this letter relies on: " + ("; ".join(grounds) if grounds else "none - this letter states facts only"),
    ])
