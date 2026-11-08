"""Citation check, in code, on every model answer. Rejects unknown or off-topic section IDs, section numbers
and Act names not backed by the corpus, anything shaped like a court case, repealed law unless corrected with
its replacement named, non-verbatim quotes, and any time period, percentage or date not in the law pack, the
computed rules or the user's facts. It cannot catch a real section cited for a wrong claim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import corpus
from .schemas import Answer

SEC_REF = re.compile(
    r"(?:\b(?:sections?|secs?\.?|s\.|u/s|dhara|article|art\.|rule|r\.)\s*\(?(\d+[a-z]?)(?:\s*\([0-9a-z]+\))*(?:\s*(?:,|and|&|or)\s*(\d+[a-z]?))*"
    r"|\b(?:bns|bnss|bsa|ipc|crpc|cpc)\s+(?:s\.?\s*)?(\d+[a-z]?)"
    r"|धारा\s*(\d+))", re.I)
CASE_REF = re.compile(
    r"(?-i:\b[A-Z][\w.&]+(?:\s+[A-Z][\w.&]+)*\s+(?:v\.?|vs\.?|Vs\.?|versus)\s+(?:State|Union|[A-Z][a-z]+)\b)"
    r"|\bAIR\s+\d{4}\b|\(\d{4}\)\s*\d+\s*SCC\b|\bSCC\s+OnLine\b|\bINSC\s+\d+|\bSCR\b|\bMANU/|\bLiveLaw\s*\("
    r"|बनाम|\b(?:supreme court|high court|apex court|tribunal)\b[^.]{0,50}\b(?:held|ruled|observed|decided|judgment|verdict|said that)\b",
    re.I)
ACT_REF = re.compile(r"\b((?:[A-Z][A-Za-z()]+\s){1,6}(?:Act|Code|Sanhita|Adhiniyam|Rules))(?:,?\s*(\d{4}))?")
CORRECTION_WORDS = ("replaced", "repealed", "repeal", "no longer", "superseded", "instead of", "khatam", "jagah", "badal gaya",
                    "purana", "old law", "older law", "earlier law", "लागू नहीं", "जगह", "खत्म", "निरस्त", "बदल")
NONEXIST = re.compile(r"(does not exist|doesn'?t exist|no such (section|provision|sub-?section|rule)|not a real (section|provision|law)|there is no (section|sub-?section|provision)|(cannot|can'?t) find (any |a )?(section|sub-?section|provision)|मौजूद नहीं|कोई धारा नहीं)", re.I)

NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty-five": 45, "forty five": 45, "sixty": 60, "ninety": 90,
            "do": 2, "teen": 3, "char": 4, "paanch": 5, "chhe": 6, "saat": 7, "das": 10, "pandrah": 15, "tees": 30}
UNIT = {"day": "d", "days": "d", "din": "d", "दिन": "d", "working day": "d", "working days": "d",
        "week": "w", "weeks": "w", "hafte": "w", "month": "m", "months": "m", "mahine": "m", "mahina": "m", "महीने": "m", "महीना": "m",
        "year": "y", "years": "y", "saal": "y", "sal": "y", "साल": "y", "varsh": "y", "वर्ष": "y"}
DUR = re.compile(r"\b(\d+|" + "|".join(sorted(NUMWORDS, key=len, reverse=True)) + r")[\s-]*(working days?|days?|weeks?|months?|years?|din|hafte|mahine|mahina|saal|sal|varsh)\b"
                 r"|(\d+)\s*(दिन|महीने|महीना|साल|वर्ष)", re.I)
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
DATE_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?,?\s+(\d{4})\b"
                     r"|\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+(\d{1,2}),?\s+(\d{4})\b"
                     r"|\b(\d{4})-(\d{2})-(\d{2})\b", re.I)


def dates_in(text: str) -> set[str]:
    out = set()
    for m in DATE_RE.finditer(text):
        g = m.groups()
        try:
            if g[0]:
                out.add(f"{int(g[2]):04d}-{MONTHS[g[1].lower()[:3]]:02d}-{int(g[0]):02d}")
            elif g[3]:
                out.add(f"{int(g[5]):04d}-{MONTHS[g[3].lower()[:3]]:02d}-{int(g[4]):02d}")
            else:
                out.add(f"{g[6]}-{g[7]}-{g[8]}")
        except (KeyError, ValueError):
            pass
    return out


PCT = re.compile(r"\b(\d+(?:\.\d+)?)\s?(?:%|percent|per cent|pratishat)", re.I)


@dataclass
class Violation:
    kind: str
    detail: str
    where: str = ""


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def feedback(self) -> str:
        return "\n".join(f"- {v.kind}: {v.detail} (in: {v.where[:120]})" for v in self.violations)


def has_word(text: str, words) -> bool:
    low = text.lower()
    return any(re.search(r"(?<![\wऀ-ॿ])" + re.escape(wd) + r"(?![\wऀ-ॿ])", low) for wd in words)


def main_number(entry: dict) -> set[str]:
    m = re.search(r"\d+[a-z]?", entry["section"].replace("Schedule, Article", ""))
    return {m.group(0).lower()} if m else set()


section_numbers = main_number  # name used by the eval grader


def durations(text: str) -> set[tuple[int, str]]:
    out = set()
    for m in DUR.finditer(text):
        n, u = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        n = int(n) if n.isdigit() else NUMWORDS.get(n.lower())
        if n:
            u = u.lower()
            out.add((n, UNIT.get(u, UNIT.get(u.rstrip("s"), "?"))))
    return out


def allowed_facts(topic: str, rule_texts: list[str]) -> tuple[set, set]:
    src = " ".join(s["text"] for s in corpus.pack(topic)) + " " + " ".join(rule_texts)
    allowed_d = durations(src) | {(15, "d")}  # the demand letter gives 15 days (drafts.py)
    allowed_p = {m.group(1) for m in PCT.finditer(src)}
    return allowed_d, allowed_p


def known_acts() -> set[str]:
    names = set()
    for s in corpus.load()["sections"]:
        names.add(re.sub(r",?\s*\d{4}.*$", "", s["act"]).lower().strip())
    for sup in corpus.superseded():
        names.add(re.sub(r",?\s*\d{4}.*$", "", sup["old"]).lower().strip())
        names |= set(sup.get("names", []))
    return names | {"industrial relations code", "code on social security", "consumer protection rules"}


def _texts(a: Answer) -> list[tuple[str, list[str]]]:
    rows = [(a.summary, [])]
    rows += [(c.text, c.cite) for c in a.claims]
    rows += [(s.action + " " + s.why, s.cite) for s in a.next_steps]
    rows += [(q, []) for q in a.questions_for_user]
    rows += [(q, []) for q in a.not_covered]
    rows += [(c, []) for c in a.corrections]
    return rows


def _is_correction(text: str, sup: dict) -> bool:
    low = text.lower()
    return (has_word(text, CORRECTION_WORDS) and any(n in low for n in sup.get("names", []))) or bool(NONEXIST.search(text))


def check_text(text: str, cites: list[str], topic: str, rule_texts: list[str] | None = None, known_dates: set[str] | None = None) -> list[Violation]:
    """All checks on one sentence. Also used by the eval grader on baseline answers."""
    sections = corpus.by_id()
    pack_ids = {s["id"] for s in corpus.pack(topic)} if topic else set(sections)
    acts = known_acts()
    allow_d, allow_p = allowed_facts(topic, rule_texts or []) if topic else (set(), set())
    out: list[Violation] = []
    low = text.lower()
    for cid in cites:
        if cid not in sections:
            out.append(Violation("unknown_id", f"'{cid}' is not a known section ID", text))
        elif cid not in pack_ids:
            out.append(Violation("off_pack", f"'{cid}' is not in the {topic} pack", text))
    allowed = set()
    for cid in cites:
        if cid in sections:
            allowed |= main_number(sections[cid])
    stale_hits = [sup for sup in corpus.superseded() if any(re.search(p, low) for p in sup["patterns"])]
    corrective = [sup for sup in stale_hits if _is_correction(text, sup)]
    for sup in corrective:  # a correct correction may name the old and the new section
        for p in sup["patterns"]:
            m = re.search(p, low)
            if m:
                allowed |= set(re.findall(r"\d+[a-z]?", m.group(0)))
        allowed |= set(re.findall(r"\d+", sup["now"].split("(")[0]))
        if sup["now_id"] in sections:
            allowed |= main_number(sections[sup["now_id"]])
    denies = bool(NONEXIST.search(text))
    for m in SEC_REF.finditer(text):
        for num in [g for g in m.groups() if g]:
            if num.lower() not in allowed and not denies:
                out.append(Violation("raw_reference", f"mentions '{m.group(0).strip()}' without citing a corpus entry for it", text))
                break
    for m in ACT_REF.finditer(text):
        name = re.sub(r"^(the|under|of|per|as|and)\s+", "", m.group(1).strip(), flags=re.I).lower()
        if not any(k in name or name in k for k in acts) and not denies:
            out.append(Violation("unknown_act", f"names '{m.group(0)}', which is not in the verified corpus", text))
    for m in CASE_REF.finditer(text):
        out.append(Violation("case_law", f"looks like a court case or ruling: '{m.group(0)[:60]}'", text))
    for sup in stale_hits:
        if sup not in corrective:
            out.append(Violation("stale_law", f"uses '{sup['old']}' without naming its replacement ({sup['now']})", text))
    for q in re.findall(r"[\"“]([^\"”]{25,})[\"”]", text):
        hay = " ".join(corpus.norm(sections[c]["text"]) for c in cites if c in sections)
        if corpus.norm(q) not in hay:
            out.append(Violation("misquote", f"quoted text not found verbatim in cited sections: \"{q[:80]}\"", text))
    if topic:
        for dur in sorted(durations(text) - allow_d):
            out.append(Violation("unsupported_time_limit", f"states a time period ({dur[0]}{dur[1]}) that is not in the law pack or the computed rules", text))
        if known_dates is not None:
            for dt in sorted(dates_in(text) - known_dates):
                out.append(Violation("unsupported_date", f"states the date {dt}, which is not in your facts or the computed rules", text))
        for m in PCT.finditer(text):
            if m.group(1) not in allow_p:
                out.append(Violation("unsupported_percent", f"states {m.group(0)} which is not in the law pack", text))
    return out


def check(a: Answer, topic: str, rule_texts: list[str] | None = None, facts_text: str = "") -> Report:
    """facts_text: confirmed facts + the person's words; any date in the answer must appear there or in the rules."""
    rep = Report()
    known = None
    if rule_texts is not None:
        src = " ".join(rule_texts) + " " + facts_text
        known = dates_in(src) | {x for x in re.findall(r"\d{4}-\d{2}-\d{2}", src)}
    sections = corpus.by_id()
    pack_ids = {s["id"] for s in corpus.pack(topic)}
    for text, cites in _texts(a):
        rep.violations += check_text(text, cites, topic, rule_texts, known)
        rep.cited += [c for c in cites if c in sections and c in pack_ids]
    rep.cited = sorted(set(rep.cited))
    return rep


def strip_bad(a: Answer, rep: Report) -> Answer:
    """Last resort after a failed retry: drop every item that carries a violation (all fields)."""
    bad = {v.where for v in rep.violations}
    return a.model_copy(update={
        "claims": [c for c in a.claims if c.text not in bad],
        "next_steps": [s for s in a.next_steps if (s.action + " " + s.why) not in bad],
        "corrections": [c for c in a.corrections if c not in bad],
        "not_covered": [c for c in a.not_covered if c not in bad],
        "questions_for_user": [q for q in a.questions_for_user if q not in bad],
        "summary": a.summary if a.summary not in bad else "Part of my answer failed the law check and was removed. Please treat what remains as partial and talk to free legal aid (15100).",
    })


DIGITS = re.compile(r"\d+")


def to_ascii_digits(t: str) -> str:
    for zero in (0x0966, 0x09E6, 0x0BE6, 0x0A66, 0x0AE6, 0x0C66, 0x0CE6, 0x0D66):
        t = "".join(chr(ord(c) - zero + 48) if zero <= ord(c) <= zero + 9 else c for c in t)
    return t


def check_rendered(text: str, cited: list[str], source: str = "") -> list[Violation]:
    """After translation (any language): no new law references or case law, and no number that is
    not in the checked English source - this works even for scripts the regexes can't read."""
    sections = corpus.by_id()
    allowed = set()
    for c in cited:
        if c in sections:
            allowed |= main_number(sections[c])
    v = []
    for m in SEC_REF.finditer(text):
        for num in [g for g in m.groups() if g]:
            if num.lower() not in allowed:
                v.append(Violation("raw_reference", f"rendering added '{m.group(0).strip()}'", text[:120]))
    for m in CASE_REF.finditer(text):
        v.append(Violation("case_law", f"rendering added '{m.group(0)[:60]}'", text[:120]))
    if source:
        def nums(t: str) -> set[str]:
            return {n.lstrip("0") or "0" for n in DIGITS.findall(to_ascii_digits(t).replace(",", ""))}
        extra = nums(text) - nums(source)
        if extra:
            v.append(Violation("number_changed", f"rendering contains numbers not in the checked source: {sorted(extra)[:6]}", text[:120]))
    return v
