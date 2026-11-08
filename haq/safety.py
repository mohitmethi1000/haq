"""Red-flag triage on the raw message, in code, before any legal help. A keyword net: it will miss new phrasings."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HELPLINES = {
    "emergency": ("Police / emergency", "112"),
    "women": ("Women helpline", "181 (in West Bengal use 112)"),
    "cyber": ("Cyber fraud helpline / cybercrime.gov.in", "1930"),
    "child": ("Child Helpline", "1098"),
    "mental": ("Tele-MANAS mental health (free, 24x7)", "14416"),
    "legal_aid": ("Free legal aid (NALSA)", "15100"),
    "tele_law": ("Tele-Law free advice", "14454"),
    "consumer": ("National Consumer Helpline", "1915"),
}

# Devanagari-safe word boundaries: \b does not work inside Devanagari words.
DV = r"ऀ-ॿ"
L, R = rf"(?<![\w{DV}])", rf"(?![\w{DV}])"


def w(p: str) -> str:
    return L + p + R



# flag: (severity, helpline keys, patterns)
RULES: dict[str, tuple[str, list[str], list[str]]] = {
    "self_harm": ("stop", ["mental", "emergency"], [
        r"suicide", w(r"kill myself"), r"end my life", r"no reason to live", r"want to die",
        r"jeene ka (mann|man) nahi", r"(sab|sab kuch|zindagi) khatam kar (doon|du|dunga|dungi)", r"jaan de (doon|dunga|dungi)",
        r"mar jaana chahta", r"mar jau", r"आत्महत्या", r"जीने का मन नहीं", r"खुद को खत्म", r"मर जाना चाहत"]),
    "violence": ("stop", ["emergency", "women"], [
        w(r"hit me") + r"(?! with)", w(r"beat(s|ing)? me"), w(r"slapped me"), r"threat(en|ened|ening)? to (kill|hurt|beat|break)",
        r"break my (legs?|hands?|bones?)", w(r"assault(ed)?"), r"domestic violence", r"locked me", w(r"maar(a|ta|ti|pit|ne|egi|ega|ega)"),
        r"jaan se maar", r"haath uthaya", w(r"मार(ा|ी|ता|ती|पीट|ेगा|ेंगे|ने)"), r"जान से मार", w(r"पीटा")]),
    "minor": ("stop", ["child"], [
        r"\bi am a minor\b", r"\bi'?m a minor\b", r"\bi am (1[0-7]|[5-9])( years?)?( old)?\b", r"\bmy (son|daughter|brother|sister|child) is (1[0-7]|[5-9])\b",
        r"\b(1[0-7]|[5-9]) (saal|sal|years?)( ka| ki| old)", r"(meri|mera|meri) (umar|umr|age) (1[0-7]|[5-9])", r"उम्र (1[0-7]|[5-9])",
        r"(1[0-7]|[5-9]) साल (का|की)", w(r"nabalig"), r"नाबालिग", r"child labou?r", r"baal mazdoor", r"बाल मजदूर"]),
    "scam": ("stop", ["cyber"], [
        r"digital arrest", r"video call.{0,40}(arrest|police|cbi|customs|narcotics)", r"\b(cbi|enforcement directorate|customs|narcotics|crime branch)\b.{0,60}(video|skype|call)",
        r"(transfer|send|pay|deposit).{0,40}(money|amount|paise|rupees|rs|₹).{0,60}(verify|verification|clear your name|safe account|rbi account)",
        r"\bparcel\b.{0,60}(drugs|mdma|illegal)", r"aadhaa?r.{0,40}(misuse|linked).{0,40}(case|crime|money laundering)",
        r"(telegram|whatsapp).{0,80}(task|rating|review|like|prepaid|part[- ]time job)", r"\btask\b.{0,60}(deposit|invest|prepaid|recharge|unlock)",
        r"(withdraw|unlock).{0,40}(fee|tax|deposit|charges)", r"loan app.{0,80}(morph|photos|contacts|threat|abuse)"]),
    "arrest_or_police": ("lawyer_now", ["legal_aid"], [
        w(r"arrest(ed)?") + r"(?! warrant scam)", r"giraftar", r"गिरफ्तार", r"police (station|case|complaint) against me", r"\bfir (against|on) me",
        r"मेरे (खिलाफ|ऊपर) (fir|एफआईआर)", r"mere (khilaf|upar) fir", r"court (summons|warrant)", r"\bwarrant\b", r"समन"]),
}

MESSAGES = {
    "self_harm": ("You matter, and you don't have to handle this alone. Please call Tele-MANAS on 14416 now - it's free and open 24 hours. "
                  "If you might act on these thoughts right now, call 112. Your money problem can wait; we can come back to it later.",
                  "आप अकेले नहीं हैं। अभी Tele-MANAS को 14416 पर कॉल करें - यह मुफ़्त है और 24 घंटे खुला है। अगर अभी खतरा है तो 112 पर कॉल करें। पैसों की बात बाद में भी हो सकती है।"),
    "violence": ("Your safety comes first. If you are in danger now, call 112. Women can also call 181. "
                 "Keep any proof (photos, messages). When you are safe, free legal aid on 15100 can help with both the violence and the money.",
                 "आपकी सुरक्षा सबसे ज़रूरी है। अभी खतरा हो तो 112 पर कॉल करें। महिलाएँ 181 पर भी कॉल कर सकती हैं। सुरक्षित होने पर 15100 पर मुफ़्त कानूनी मदद लें।"),
    "minor": ("Because you (or the person) may be under 18, please call the Child Helpline on 1098. They can help get unpaid wages and keep the child safe. "
              "Employing a child is itself illegal, so this needs a person, not an app.",
              "अगर उम्र 18 साल से कम है, तो 1098 (चाइल्ड हेल्पलाइन) पर कॉल करें। वे बकाया पैसे और बच्चे की सुरक्षा - दोनों में मदद करेंगे।"),
    "scam": ("This looks like a scam. No police, CBI, court or real employer asks you to pay money, stay on a video call, or keep it secret. "
             "Stop talking to them and do not pay anything more. Call 1930 or report at cybercrime.gov.in. If you already paid, call 1930 immediately - "
             "the sooner you report, the better the chance of freezing the money.",
             "यह धोखाधड़ी (स्कैम) लगती है। कोई पुलिस, CBI या असली कंपनी पैसे भेजने या वीडियो कॉल पर रहने को नहीं कहती। और पैसे न भेजें। 1930 पर कॉल करें या cybercrime.gov.in पर शिकायत करें। अगर पैसे भेज चुके हैं तो तुरंत 1930 पर कॉल करें।"),
    "arrest_or_police": ("Because police, arrest or a court is involved, please talk to a lawyer before doing anything. Free legal aid: 15100. "
                         "I can still explain your money problem below, but don't rely on me for the police part.",
                         "पुलिस, गिरफ्तारी या कोर्ट का मामला है, इसलिए पहले वकील से बात करें। मुफ़्त कानूनी मदद: 15100।"),
}

MODEL_FLAG_MAP = [  # model's free-text red flags -> category
    ("self_harm", ("self-harm", "self harm", "suicid", "kill myself")),
    ("violence", ("violence", "assault", "beaten", "hit ", "physical", "threat to kill", "domestic")),
    ("minor", ("minor", "child", "under 18", "underage")),
    ("scam", ("scam", "fraud", "money transfer", "digital arrest", "extortion")),
    ("arrest_or_police", ("arrest", "police", "fir", "summons", "warrant")),
]


@dataclass
class Triage:
    flags: list[str] = field(default_factory=list)
    severity: str = "none"  # none | lawyer_now | stop
    helplines: list[tuple[str, str]] = field(default_factory=list)
    messages: list[tuple[str, str]] = field(default_factory=list)  # (english, hindi)

    @property
    def blocks_drafting(self) -> bool:
        return self.severity == "stop"


def normalise(text: str) -> str:
    """Lower-case and turn Devanagari/Bengali/Tamil digits into ASCII so ages and amounts match."""
    t = text.lower()
    for zero in (0x0966, 0x09E6, 0x0BE6, 0x0A66, 0x0AE6, 0x0C66, 0x0CE6, 0x0D66):
        t = "".join(chr(ord(c) - zero + 48) if zero <= ord(c) <= zero + 9 else c for c in t)
    return t


def _negated(t: str, m: re.Match) -> bool:
    """'even though he didn't hit me' / 'maara nahi' -> not a real report.
    Tight on purpose: Hinglish uses "nahi" all the time ("salary nahi di"), so "nahi" only counts
    right after the violence word; English negations only within ~40 characters around it."""
    before = t[max(0, m.start() - 40): m.start()]
    after = t[m.end(): m.end() + 35]
    eng = r"(didn'?t|did not|never|even though|although|not really)"
    return bool(re.search(eng, before)) or bool(re.search(eng, after)) or bool(re.match(r"\W{0,3}(nahi|nahin|नहीं)", after))


def triage(text: str, model_flags: list[str] | None = None) -> Triage:
    t = normalise(text)
    tri = Triage()
    order = {"none": 0, "lawyer_now": 1, "stop": 2}

    def add(flag: str, source: str) -> None:
        sev, hls, _ = RULES[flag]
        if flag not in [f.split(":")[-1] for f in tri.flags]:
            tri.flags.append(f"{source}:{flag}" if source == "model" else flag)
            tri.helplines += [HELPLINES[h] for h in hls]
            tri.messages.append(MESSAGES[flag])
        if order[sev] > order[tri.severity]:
            tri.severity = sev

    for flag, (_, _, pats) in RULES.items():
        for p in pats:
            m = re.search(p, t)
            if m and not (flag == "violence" and _negated(t, m)):
                add(flag, "kw")
                break
    for mf in model_flags or []:
        m = mf.lower()
        for flag, keys in MODEL_FLAG_MAP:
            if any(k in m for k in keys):
                add(flag, "model")
                break
    if tri.flags:
        tri.helplines.append(HELPLINES["legal_aid"])
    seen, hl = set(), []
    for h in tri.helplines:
        if h not in seen:
            seen.add(h)
            hl.append(h)
    tri.helplines = hl
    return tri
