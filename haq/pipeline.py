"""The flow, as a small state machine:
extract -> user confirms -> triage -> rules -> answer -> validate (one retry, then strip) -> support check -> render -> letter.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Optional, get_args, get_origin

from pydantic import BaseModel

from . import corpus, drafts, prompts, rules, safety, validate
from .llm import LLM
from .schemas import Answer, Facts, Rendered

COVERED = {"refund", "salary", "deposit"}


SCRIPTS = {"hindi": (0x0900, 0x097F), "bengali": (0x0980, 0x09FF), "punjabi": (0x0A00, 0x0A7F), "gujarati": (0x0A80, 0x0AFF),
           "tamil": (0x0B80, 0x0BFF), "telugu": (0x0C00, 0x0C7F), "kannada": (0x0C80, 0x0CFF), "malayalam": (0x0D00, 0x0D7F)}


def detect_language(raw: str, model_label: str | None) -> str:
    """Decide the language from the script; the model's label only separates Hinglish from Roman Hindi."""
    letters = [c for c in raw if c.isalpha()]
    if not letters:
        return "english"
    counts = {k: sum(lo <= ord(c) <= hi for c in letters) for k, (lo, hi) in SCRIPTS.items()}
    best = max(counts, key=counts.get)
    if counts[best] / len(letters) > 0.3:
        lab = (model_label or "").lower()
        return "marathi" if best == "hindi" and "marathi" in lab else best
    lab = (model_label or "").lower()
    if "roman" in lab:
        return "roman_hindi"
    if lab.startswith("hi") or "hinglish" in lab or "hindi" in lab:
        return "hinglish"
    return "english"


def schema_hint(model: type[BaseModel]) -> str:
    def t(ann: Any) -> Any:
        o = get_origin(ann)
        if o is list:
            (inner,) = get_args(ann) or (str,)
            return [t(inner)]
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            return {k: t(v.annotation) for k, v in ann.model_fields.items()}
        args = [a for a in get_args(ann) if a is not type(None)]
        if o is not None and args:
            if all(isinstance(a, str) for a in args):
                return " | ".join(args)
            return t(args[0]) if len(args) == 1 else str(ann)
        return getattr(ann, "__name__", str(ann)).replace("NoneType", "null")
    hint = {k: t(v.annotation) for k, v in model.model_fields.items()}
    return json.dumps(hint, ensure_ascii=False)


@dataclass
class Advice:
    facts: dict
    triage: dict
    rules: list[dict] = field(default_factory=list)
    answer: Optional[dict] = None
    answer_first_try: Optional[dict] = None
    violations_first_try: list[dict] = field(default_factory=list)
    violations_final: list[dict] = field(default_factory=list)
    support: list[dict] = field(default_factory=list)
    rendered: Optional[dict] = None
    render_violations: list[dict] = field(default_factory=list)
    draft: str = ""
    escalate: bool = False
    escalation_reasons: list[str] = field(default_factory=list)
    helplines: list[list[str]] = field(default_factory=list)
    scope_message: str = ""
    scope_message_hi: str = ""
    cited: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    draft_notes: str = ""
    versions: dict = field(default_factory=dict)

    def dict(self) -> dict:
        return asdict(self)


# ---------------- step 1: extract ----------------
def extract(llm: LLM, message: str, today: date, previous: Optional[Facts] = None) -> Facts:
    prev = ""
    if previous:
        prev = ("\nFacts already confirmed earlier (keep them unless the new message changes them; merge new details in):\n"
                + previous.model_dump_json(exclude_defaults=True))
    sys = prompts.EXTRACT.format(today=today.isoformat(), previous=prev, schema=schema_hint(Facts))
    f = llm.chat_model([{"role": "system", "content": sys}, {"role": "user", "content": message}], Facts, "extract")
    raw = message if not previous else message
    return f.model_copy(update={"user_language": detect_language(raw, f.user_language) if not previous else previous.user_language})


# ---------------- step 2..5: advise ----------------
def advise(llm: LLM, facts: Facts, raw_text: str, today: date, support_check: bool = True,
           render: bool = True) -> Advice:
    tri = safety.triage(raw_text, facts.red_flags)
    adv = Advice(facts=facts.model_dump(), triage=asdict(tri), helplines=[list(h) for h in tri.helplines])

    if tri.severity == "stop":
        adv.escalate = True
        adv.escalation_reasons.append(f"safety: {', '.join(tri.flags)}")
        adv.scope_message = "\n\n".join(en for en, _ in tri.messages)
        adv.scope_message_hi = "\n\n".join(hi for _, hi in tri.messages)
        return adv
    if tri.severity == "lawyer_now":
        adv.escalate = True
        adv.escalation_reasons.append(f"needs a lawyer: {', '.join(tri.flags)}")
        adv.scope_message = "\n\n".join(en for en, _ in tri.messages)
        adv.scope_message_hi = "\n\n".join(hi for _, hi in tri.messages)

    if facts.dispute_type not in COVERED:
        adv.escalate = True
        adv.escalation_reasons.append(f"out of scope: {facts.dispute_type}")
        if facts.dispute_type == "notice":
            adv.scope_message = (
                "I can't verify notices yet, but here is a safe checklist: (1) A real court summons has a case number you can check on ecourts.gov.in; police and consumer-commission notices have an official reference you can confirm by calling that office on a number you find yourself. "
                "(2) No real agency asks you to pay money, stay on a video call, or keep it secret. If that is happening, it is a scam: call 1930. "
                "(3) A legal notice from a lawyer is a demand letter, not a court order; note its deadline and get free help on 15100 or Tele-Law 14454 before replying.")
            adv.helplines += [list(safety.HELPLINES["cyber"]), list(safety.HELPLINES["legal_aid"]), list(safety.HELPLINES["tele_law"])]
        else:
            adv.scope_message = ("This problem is outside what I have verified law for (I cover unpaid salary, consumer refunds and, partly, rental deposits). "
                                 "Rather than guess, please contact free legal aid: NALSA 15100 or Tele-Law 14454. They can advise you at no cost.")
            adv.helplines += [list(safety.HELPLINES["legal_aid"]), list(safety.HELPLINES["tele_law"])]
        adv.helplines = _dedupe(adv.helplines)
        return adv

    # rules (code)
    rr = rules.run(facts, today)
    adv.rules = [r.dict() for r in rr]
    for r in rr:
        if r.escalate:
            adv.escalate = True
            adv.escalation_reasons.append(f"rule: {r.name} ({r.outcome})")

    # answer (model, constrained to pack) + firewall + one retry
    topic = facts.dispute_type
    sys = prompts.ANSWER.format(topic=topic, pack=corpus.pack_text(topic), schema=schema_hint(Answer))
    user = ("CONFIRMED FACTS:\n" + facts.model_dump_json(exclude_defaults=True)
            + "\n\nRULE RESULTS:\n" + json.dumps([{k: r[k] for k in ("name", "outcome", "message", "cite")} for r in adv.rules], ensure_ascii=False)
            + "\n\nTHE PERSON'S OWN WORDS:\n" + raw_text[:3000])
    msgs = [{"role": "system", "content": sys}, {"role": "user", "content": user}]
    rule_texts = [r["message"] for r in adv.rules] + [r["deadline"] for r in adv.rules if r.get("deadline")]
    facts_text = facts.model_dump_json() + " " + raw_text
    ans = llm.chat_model(msgs, Answer, "answer")
    rep = validate.check(ans, topic, rule_texts, facts_text)
    adv.answer_first_try = ans.model_dump()
    adv.violations_first_try = [asdict(v) for v in rep.violations]
    if not rep.ok:
        retry = msgs + [{"role": "assistant", "content": ans.model_dump_json()},
                        {"role": "user", "content": "Your answer failed the law check:\n" + rep.feedback()
                         + "\nFix ONLY these problems (cite only pack IDs; remove case law; no uncited section numbers; correct old laws). Return the full corrected JSON."}]
        ans = llm.chat_model(retry, Answer, "answer:retry")
        rep = validate.check(ans, topic, rule_texts, facts_text)
        if not rep.ok:
            adv.violations_final = [asdict(v) for v in rep.violations]
            ans = validate.strip_bad(ans, rep)
            adv.escalate = True
            adv.escalation_reasons.append("validator: answer still failed after one retry; failing sentences removed")
            rep = validate.check(ans, topic, rule_texts, facts_text)
    adv.answer = ans.model_dump()
    adv.cited = rep.cited

    # support check (separate cheap model call): does the cited text back the sentence?
    # Flagged sentences are shown as "not fully confirmed", not as law.
    if support_check:
        adv.support = support(llm, ans)
        flagged = {s["text"] for s in adv.support if s.get("supported") is False}
        adv.unconfirmed = [c.text for c in ans.claims if c.text in flagged]
        if flagged:
            adv.escalation_reasons.append(f"support check could not confirm {len(adv.unconfirmed)} sentence(s); shown separately as 'not fully confirmed'")
        if any(s.get("supported") is None for s in adv.support):
            adv.escalation_reasons.append("support check unavailable for some sentences (treated as not confirmed)")

    # render into the user's language, then re-check: no new law, no changed numbers
    lang = (facts.user_language or "english").lower()
    if render and lang not in ("english", "en"):
        label = {"hindi": "Hindi (Devanagari script)", "hinglish": "Hinglish", "roman_hindi": "Roman Hindi (Hindi in Roman letters, like the person wrote)"}.get(lang, lang.title())
        src = {"summary": ans.summary, "claims": [c.text for c in ans.claims] + ans.corrections,
               "next_steps": [f"{s.action} — {s.why}" for s in ans.next_steps], "questions_for_user": ans.questions_for_user}
        src_text = json.dumps(src, ensure_ascii=False)
        try:
            r = llm.chat_model([{"role": "user", "content": prompts.RENDER.format(language=label, source=src_text)}], Rendered, "render")
            flat = " ".join([r.summary] + r.claims + r.next_steps + r.questions_for_user)
            adv.render_violations = [asdict(v) for v in validate.check_rendered(flat, rep.cited, src_text + " " + " ".join(x["message"] for x in adv.rules))]
            if len(r.claims) != len(src["claims"]) or len(r.next_steps) != len(src["next_steps"]):
                adv.render_violations.append({"kind": "count_mismatch", "detail": "translation changed the number of items", "where": ""})
            if adv.render_violations:
                adv.escalation_reasons.append("translation failed a check; showing the checked English instead")
            else:
                adv.rendered = r.model_dump()
        except Exception as e:  # a failed translation must never lose the checked English answer
            adv.render_violations.append({"kind": "render_error", "detail": str(e)[:200], "where": ""})
            adv.escalation_reasons.append("translation unavailable; showing the checked English")

    if not tri.blocks_drafting:
        adv.draft = drafts.demand_letter(facts, rr, today)
        adv.draft_notes = drafts.sending_notes(facts, rr)
    adv.versions = versions(llm)
    if adv.escalate or facts.missing:
        adv.helplines += [list(safety.HELPLINES["legal_aid"]), list(safety.HELPLINES["tele_law"])]
    if topic == "refund":
        adv.helplines.append(list(safety.HELPLINES["consumer"]))
    adv.helplines = _dedupe(adv.helplines)
    return adv


def support(llm: LLM, ans: Answer) -> list[dict]:
    secs = corpus.by_id()
    items, idx = [], []
    for c in ans.claims:
        for cid in c.cite:
            if cid in secs:
                idx.append((len(idx) + 1, c.text, cid))
    if not idx:
        return []
    for n, text, cid in idx:
        items.append(f"{n}. SENTENCE: {text}\n   LAW TEXT [{cid}]: {secs[cid]['text'][:6000]}")
    try:
        out = llm.chat([{"role": "user", "content": prompts.SUPPORT.format(items="\n".join(items))}], "support", json_mode=True)
        from .llm import extract_json
        rows = extract_json(out).get("results", [])
        # The model sometimes drops "n" or returns strings; map by n when valid, else by position.
        res = {}
        for k, r in enumerate(rows):
            n = r.get("n")
            n = int(n) if isinstance(n, (int, str)) and str(n).strip().isdigit() else k + 1
            sup = r.get("supported")
            if isinstance(sup, str):
                sup = {"true": True, "yes": True, "false": False, "no": False}.get(sup.strip().lower())
            res[n] = {"supported": sup if isinstance(sup, bool) else None, "reason": r.get("reason", "")}
    except Exception as e:  # warning light only; never block the answer, but say it was unavailable
        return [{"n": n, "text": t, "cite": c, "supported": None, "reason": f"check failed: {e}"} for n, t, c in idx]
    return [{"n": n, "text": t, "cite": c, "supported": res.get(n, {}).get("supported"), "reason": res.get(n, {}).get("reason", "")} for n, t, c in idx]


def versions(llm: LLM) -> dict:
    import hashlib
    ptxt = "".join(getattr(prompts, k) for k in dir(prompts) if k.isupper())
    return {"model": f"{llm.cfg.provider}/{llm.cfg.model}", "corpus_as_of": corpus.load()["as_of"],
            "corpus_sha": hashlib.sha256(corpus.CORPUS.read_bytes()).hexdigest()[:12],
            "prompts_sha": hashlib.sha256(ptxt.encode()).hexdigest()[:12]}


def _dedupe(rows: list[list[str]]) -> list[list[str]]:
    seen, out = set(), []
    for r in rows:
        k = tuple(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


# ---------------- one-shot helper used by the CLI and the eval runner ----------------
def run_case(llm: LLM, story: str, today: date, fact_overrides: Optional[dict] = None, **kw) -> dict:
    """Simulates the UI: extract -> (user confirms; tests may override facts) -> advise."""
    facts = extract(llm, story, today)
    if fact_overrides:
        facts = facts.model_copy(update=fact_overrides)
    adv = advise(llm, facts, story, today, **kw)
    return {"story": story, "today": today.isoformat(), **adv.dict()}
