"""Streamlit UI. Run: streamlit run app.py"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from pathlib import Path

import streamlit as st

from haq import corpus, pipeline, prompts
from haq.llm import LLM, LOG, PRESETS, LLMError, ModelConfig, load_env
from haq.rules import d as parse_date
from haq.schemas import Facts

load_env()
try:  # Streamlit Cloud secrets -> env, so the same code runs locally and hosted
    for k, v in st.secrets.items():
        os.environ.setdefault(k, str(v))
except Exception:
    pass


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


MAX_CALLS = _int_env("MAX_CALLS_PER_SESSION", 40)
DAILY_CAP = _int_env("DAILY_CALL_CAP", 400)
MAX_CHARS = 3000

st.set_page_config(page_title="Haq - help getting your money back", page_icon="⚖️", layout="wide")
ss = st.session_state
for k, v in {"stage": "intake", "story": "", "facts": None, "advice": None, "calls": 0, "practice": [], "ui": "English"}.items():
    ss.setdefault(k, v)

T = {
    "English": {
        "tagline": "Get back money you're owed: **unpaid salary**, a **refund that never came**, or (partly) a **rental deposit**. Speak or type in English, Hindi or Hinglish.",
        "notlawyer": "I am not a lawyer and not a government service. I explain what the law text says, work out your deadlines and draft a letter. Free government legal help: **15100**.",
        "privacy": "Don't type real phone numbers, Aadhaar or bank details. Your words are sent to an AI model to understand them; this app does not save them.",
        "s1": "1. Tell me what happened", "s2": "2. Check your case file", "s3": "3. Where you stand", "s4": "4. Your draft letter",
        "next": "Next: build my case file ➜", "confirm": "✅ These facts are right - show where I stand", "back": "⬅ Back", "change": "✏️ Change my facts",
        "check": "I may have misunderstood something. **Please check and correct** - everything after this uses only these facts.",
        "lawsays": "What the law says", "unconf": "Not fully confirmed by the law text - please check these with free legal aid (15100)",
        "todo": "What you can do next", "ask": "I'd like to know", "cant": "What I can't tell you", "help": "Free help",
        "person": "Please also talk to a person about this.", "letter_cap": "Made only from the facts you confirmed. Fill every [FILL], remove anything untrue, then you send it. I never send anything.",
    },
    "हिंदी": {
        "tagline": "अपना बकाया पैसा वापस पाएँ: **रुकी हुई सैलरी**, **रिफ़ंड जो नहीं मिला**, या (कुछ हद तक) **किराए का डिपॉज़िट**। हिंदी, Hinglish या English में बोलें या लिखें।",
        "notlawyer": "मैं वकील नहीं हूँ और यह सरकारी ऐप नहीं है। मैं बताता हूँ कि कानून में क्या लिखा है, आपकी आख़िरी तारीख़ निकालता हूँ और चिट्ठी बनाता हूँ। मुफ़्त सरकारी कानूनी मदद: **15100**।",
        "privacy": "असली फ़ोन नंबर, आधार या बैंक डिटेल न लिखें। आपकी बात समझने के लिए AI मॉडल को भेजी जाती है; यह ऐप उसे सेव नहीं करता।",
        "s1": "1. बताइए क्या हुआ", "s2": "2. अपनी केस फ़ाइल जाँचें", "s3": "3. आपकी स्थिति", "s4": "4. आपकी चिट्ठी (ड्राफ़्ट)",
        "next": "आगे: मेरी केस फ़ाइल बनाओ ➜", "confirm": "✅ ये बातें सही हैं - आगे बताओ", "back": "⬅ पीछे", "change": "✏️ जानकारी बदलें",
        "check": "हो सकता है मैंने कुछ गलत समझा हो। **कृपया जाँचें और ठीक करें** - आगे सब कुछ इन्हीं बातों पर आधारित होगा।",
        "lawsays": "कानून क्या कहता है", "unconf": "कानून के शब्दों से पूरी तरह पक्का नहीं - इन्हें मुफ़्त कानूनी मदद (15100) से ज़रूर पूछें",
        "todo": "आप आगे क्या कर सकते हैं", "ask": "मुझे यह जानना है", "cant": "जो मैं नहीं बता सकता", "help": "मुफ़्त मदद",
        "person": "इस बारे में किसी व्यक्ति से भी ज़रूर बात करें।", "letter_cap": "सिर्फ़ आपकी पक्की की गई जानकारी से बनी है। हर [FILL] भरें, जो सच नहीं है उसे हटाएँ, फिर आप ख़ुद भेजें। मैं कुछ नहीं भेजता।",
    },
}

# ---------------- sidebar ----------------
with st.sidebar:
    ss.ui = st.radio("भाषा / Language", ["English", "हिंदी"], index=0 if ss.ui == "English" else 1, horizontal=True)
    today = st.date_input("Today's date", value=date.today(), help="Used for deadline maths. Change only for testing.")
    if st.button("🔄 Start over"):
        for k in ("stage", "story", "facts", "advice", "practice"):
            ss.pop(k, None)
        st.rerun()
    with st.expander("Advanced: model settings (for testers)"):
        env_cfg = ModelConfig.from_env()
        names = list(PRESETS) + ["custom"]
        provider = st.selectbox("Model provider", names, index=names.index(env_cfg.provider) if env_cfg.provider in names else 0)
        preset = PRESETS.get(provider, ("", "", "LLM_API_KEY", 0, 0))
        key_in = st.text_input("Your API key", type="password", help="With your own key you can point to any OpenAI-compatible endpoint.")
        model = st.text_input("Model name", value=env_cfg.model if provider == env_cfg.provider else preset[1])
        base_url = st.text_input("Base URL", value=env_cfg.base_url if provider == env_cfg.provider else preset[0], disabled=not key_in,
                                 help="Editable only with your own key, so the server's key is never sent to an unknown address.")
        st.caption(f"Model calls this session: {ss.calls}/{MAX_CALLS}")

t = T[ss.ui]


def get_llm() -> LLM:
    if key_in:  # visitor's own key: any endpoint
        cfg = ModelConfig(provider=provider, model=model, base_url=base_url, api_key=key_in)
    else:  # server key only ever goes to its configured endpoint
        cfg = ModelConfig.from_env()
        if provider != cfg.provider and provider in PRESETS and os.environ.get(PRESETS[provider][2]):
            cfg = ModelConfig(provider=provider, model=model or PRESETS[provider][1], base_url=PRESETS[provider][0], api_key=os.environ[PRESETS[provider][2]])
    cfg.reasoning = os.environ.get("LLM_REASONING", "off")
    return LLM(cfg, tag="app")


def calls_today() -> int:
    if not LOG.exists():
        return 0
    day = time.strftime("%Y-%m-%d")
    n = 0
    with LOG.open() as f:
        for line in f:
            if f'"ts": "{day}' in line and '"tag": "app"' in line:
                n += 1
    return n


def budget_ok(n: int) -> bool:
    if key_in:
        return True
    if ss.calls + n > MAX_CALLS or calls_today() + n > DAILY_CAP:
        st.error("This free demo has reached its usage limit. Please try later, or add your own API key under Advanced settings.")
        return False
    ss.calls += n
    return True


def tel(label: str, number: str) -> str:
    digits = re.match(r"\d+", number)
    return f"[{label}: **{number}**](tel:{digits.group(0)})" if digits else f"{label}: **{number}**"


def date_field(col, label: str, key: str, value, hint: str = "e.g. 2026-09-30") -> str | None:
    raw = col.text_input(label, value or "", placeholder=hint, key=key)
    if raw and not parse_date(raw):
        col.caption("⚠️ Please write the date like 2026-09-30 (year-month-day).")
    return raw.strip() or None


def run_safely(fn, *a, **k):
    try:
        return fn(*a, **k)
    except LLMError as e:
        st.error(f"The AI model didn't respond properly. Please try again in a minute. ({str(e)[:160]})")
    except Exception as e:  # never show a traceback to a user
        st.error(f"Something went wrong on our side. Please try again. ({type(e).__name__})")
    return None


# ---------------- header ----------------
st.title("⚖️ Haq")
st.markdown(t["tagline"])
st.info(t["notlawyer"] + "  \n" + t["privacy"])

tab_main, tab_practice, tab_how = st.tabs(["🗂️ My problem / मेरी समस्या", "🎭 Practise / अभ्यास", "🔍 How this works"])

KIND_LABEL = {"company": "Company / employer", "online_platform": "Online shop / app", "individual": "A person (e.g. landlord)", "government": "Government office", "unknown": "Not sure"}
JOB_LABEL = {"still_employed": "Still working there", "resigned": "I resigned", "terminated": "I was removed / fired", "unknown": "Not sure"}
WORK_LABEL = {"employee": "Employee (monthly salary)", "freelance_or_contract": "Freelancer / paid per invoice", "gig_platform": "Delivery / ride app worker",
              "domestic_worker": "Domestic worker (in a home)", "government_employee": "Government employee", "agency_worker": "Hired through an agency / contractor", "unknown": "Not sure"}
TYPE_LABEL = {"salary": "Unpaid salary", "refund": "Refund not given", "deposit": "Deposit not returned", "notice": "I got a notice / threatening call", "other": "Something else"}
REASON = [("safety", "your safety"), ("needs a lawyer", "police or court is involved"), ("out of scope", "this is outside what I cover"),
          ("deposit_scope", "your State's rent law decides this"), ("limitation", "a time limit is close or has passed"), ("pre_code", "older months may follow an older law"),
          ("worker_status", "it's unclear if the wage law covers your kind of work"), ("forum_and_fee", "the amount is large"), ("consumer_status", "consumer law may not apply"),
          ("government", "the other side is a government body"), ("pf_not", "PF was not deposited"), ("validator", "part of my answer failed the law check"),
          ("support check", "some sentences couldn't be confirmed from the law text"), ("translation", "the translation failed a check")]


def friendly(reasons: list[str]) -> list[str]:
    out = []
    for r in reasons:
        for key, text in REASON:
            if key in r and text not in out:
                out.append(text)
    return out


# ======================= main flow =======================
with tab_main:
    if ss.stage == "intake":
        st.subheader(t["s1"])
        st.caption("Example: *Meri company ne August aur September ki salary nahi di, 35000 per month tha. Maine 30 September ko resign kiya.*")
        audio = st.audio_input("🎙️ Speak (up to ~30 seconds)")
        if audio is not None and st.button("Use my recording"):
            if len(audio.getvalue()) > 2_000_000:
                st.warning("That recording is too long. Please keep it under 30 seconds, or type.")
            elif budget_ok(1):
                from haq import stt
                out = run_safely(stt.transcribe, audio.getvalue())
                if out:
                    ss.story = (ss.story + " " + out["text"]).strip()[:MAX_CHARS]
                    st.success(f"I heard: {out['text']}")
        story = st.text_area("Or type it here", value=ss.story, height=150, max_chars=MAX_CHARS,
                             placeholder="What happened, when, how much money, what proof you have...")
        if st.button(t["next"], type="primary", disabled=not story.strip()) and budget_ok(2):
            ss.story = story.strip()
            with st.spinner("Reading your story..."):
                f = run_safely(pipeline.extract, get_llm(), ss.story, today)
            if f:
                ss.facts = f.model_dump()
                ss.stage = "confirm"
                st.rerun()

    elif ss.stage == "confirm":
        f = ss.facts
        st.subheader(t["s2"])
        st.info(t["check"])
        c1, c2, c3 = st.columns(3)
        types = list(TYPE_LABEL)
        f["dispute_type"] = c1.selectbox("What is the problem?", types, index=types.index(f["dispute_type"]), format_func=TYPE_LABEL.get)
        f["other_party_name"] = c2.text_input("Name of the other side", f.get("other_party_name") or "") or None
        kinds = list(KIND_LABEL)
        f["other_party_kind"] = c3.selectbox("The other side is", kinds, index=kinds.index(f.get("other_party_kind", "unknown")), format_func=KIND_LABEL.get)
        c1, c2, c3 = st.columns(3)
        f["user_state"] = c1.text_input("Your State", f.get("user_state") or "") or None
        f["user_city"] = c2.text_input("Your city", f.get("user_city") or "") or None
        amt = c3.number_input("Money you want back (₹)", min_value=0.0, value=max(0.0, float(f.get("amount_claimed_inr") or 0)), step=500.0)
        f["amount_claimed_inr"] = amt or None
        if f["dispute_type"] == "salary":
            c1, c2, c3 = st.columns(3)
            works = list(WORK_LABEL)
            f["work_relationship"] = c1.selectbox("How did you work?", works, index=works.index(f.get("work_relationship", "unknown")), format_func=WORK_LABEL.get)
            f["monthly_wage_inr"] = c2.number_input("Monthly salary (₹)", min_value=0.0, value=max(0.0, float(f.get("monthly_wage_inr") or 0)), step=500.0) or None
            ends = list(JOB_LABEL)
            f["employment_end"] = c3.selectbox("Are you still working there?", ends, index=ends.index(f.get("employment_end", "unknown")), format_func=JOB_LABEL.get)
            c1, c2, c3 = st.columns(3)
            f["unpaid_from_month"] = date_field(c1, "First unpaid month", "m1", f.get("unpaid_from_month"), "e.g. 2026-08")
            f["unpaid_to_month"] = date_field(c2, "Last unpaid month", "m2", f.get("unpaid_to_month"), "e.g. 2026-09")
            f["employment_end_date"] = date_field(c3, "Your last working day", "ed", f.get("employment_end_date"))
            f["pf_deducted_not_deposited"] = st.checkbox("PF was cut from my salary but not deposited", bool(f.get("pf_deducted_not_deposited")))
        elif f["dispute_type"] == "refund":
            c1, c2, c3 = st.columns(3)
            f["amount_paid_inr"] = c1.number_input("Price you paid (₹)", min_value=0.0, value=max(0.0, float(f.get("amount_paid_inr") or 0)), step=100.0) or None
            f["purchase_date"] = date_field(c2, "Date you paid", "pd", f.get("purchase_date"))
            f["refund_refused_date"] = date_field(c3, "First date they refused the refund (if any)", "rd", f.get("refund_refused_date"))
            f["bought_for_business"] = st.checkbox("I bought this for a business (not personal use)", bool(f.get("bought_for_business")))
        elif f["dispute_type"] == "deposit":
            c1, c2 = st.columns(2)
            f["deposit_paid_date"] = date_field(c1, "Date you paid the deposit", "dd", f.get("deposit_paid_date"))
            f["moved_out_date"] = date_field(c2, "Date you moved out (leave empty if you still live there)", "mo", f.get("moved_out_date"))
        ev = st.text_input("Proof you have (e.g. salary slip, chats, bill)", ", ".join(f.get("evidence") or []))
        f["evidence"] = [e.strip() for e in ev.split(",") if e.strip()]
        if f.get("timeline"):
            with st.expander("What I understood, in order"):
                for e in f["timeline"]:
                    st.markdown(f"- **{e.get('date') or 'date?'}** - {e['event']}" + (f"  \n  _you said: \"{e['user_words']}\"_" if e.get("user_words") else ""))
        if f.get("missing"):
            st.warning("Still unknown: " + "; ".join(f["missing"]))
        more = st.text_input("Anything to add or correct, in your own words (optional)", max_chars=1000)
        b1, b2 = st.columns([1, 3])
        if b1.button(t["back"]):
            ss.stage = "intake"
            st.rerun()
        if b2.button(t["confirm"], type="primary") and budget_ok(6):
            def go():
                llm = get_llm()
                facts = Facts.model_validate(f)
                raw = ss.story
                if more.strip():
                    raw += "\n" + more.strip()
                    facts = pipeline.extract(llm, more.strip(), today, previous=facts)
                return facts, pipeline.advise(llm, facts, raw, today).dict()
            with st.spinner("Checking the law and working out your deadlines..."):
                res = run_safely(go)
            if res:
                ss.facts, ss.advice = res[0].model_dump(), res[1]
                ss.stage = "advice"
                st.rerun()

    elif ss.stage == "advice":
        a = ss.advice
        secs = corpus.by_id()
        hi = ss.ui == "हिंदी"
        if a["triage"]["severity"] == "stop":
            st.error(a.get("scope_message_hi") if hi and a.get("scope_message_hi") else a["scope_message"])
        elif a["triage"]["severity"] == "lawyer_now":
            st.warning(a.get("scope_message_hi") if hi and a.get("scope_message_hi") else a["scope_message"])
        elif a["escalate"]:
            why = friendly(a["escalation_reasons"])
            st.warning(f"**{t['person']}** " + (a["scope_message"] or ("Why: " + "; ".join(why) + ". Free legal aid: 15100.")))
        if a["helplines"]:
            st.markdown(f"**{t['help']}:** " + " · ".join(tel(n, num) for n, num in a["helplines"]))

        if a["answer"]:
            ans, ren = a["answer"], a.get("rendered")
            use_ren = bool(ren) and (hi or a["facts"].get("user_language") not in ("english",))
            if ren:
                use_ren = st.toggle("Show in my language" if not hi else "मेरी भाषा में दिखाएँ", value=use_ren)
            st.subheader(t["s3"])
            st.markdown(ren["summary"] if use_ren else ans["summary"])

            deadlines = [r for r in a["rules"] if r.get("deadline") and r["outcome"] in ("within", "out_of_time", "partly_out_of_time")]
            for r in deadlines:
                x = parse_date(r["deadline"])
                if r["outcome"] == "out_of_time":
                    st.error(f"**{r.get('label') or 'Time limit'}: passed on {x:%d %b %Y}.** {r['message']}")
                else:
                    st.success(f"**{r.get('label') or 'Last date'}: {x:%d %b %Y}** ({r['days_left']} days left). {r['message']}")
            for r in a["rules"]:
                if r in deadlines or r["name"] in ("wage_due_date",):
                    continue
                icon = {"info": "ℹ️", "uncertain": "❓", "blocked": "⛔"}.get(r["outcome"], "•")
                st.markdown(f"{icon} **{r.get('label') or ''}** {r['message']}")

            st.markdown(f"#### {t['lawsays']}")
            unconf = set(a.get("unconfirmed") or [])
            src_claims = ans["claims"] + [{"text": c, "cite": []} for c in ans["corrections"]]
            shown = ren["claims"] if use_ren else [c["text"] for c in src_claims]
            flagged = []
            for i, text in enumerate(shown):
                src = src_claims[i] if i < len(src_claims) else {"text": text, "cite": []}
                if src["text"] in unconf:
                    flagged.append(text)
                    continue
                st.markdown(f"- {text}")
                for cid in src["cite"]:
                    s = secs.get(cid)
                    if not s:
                        continue
                    badge = ("📜 Official Gazette text (exact copy)" if s["source_kind"].startswith("Gazette")
                             else "📄 Exact copy from a legal website (not the official Gazette)" if s["kind"].startswith("verbatim")
                             else "📝 Summary of official rules")
                    with st.expander(f"{s['act']} {s['section']} · {badge}"):
                        st.caption(f"{s['heading']} · in force from {s['effective_from']} · source: {s['source_kind']} · as of {s['as_of']} · not reviewed by a lawyer")
                        st.markdown(f"> {s['text']}")
                        st.markdown(f"[Open source]({s['source_url']})")
            if flagged:
                st.markdown(f"**⚠️ {t['unconf']}:**")
                for x in flagged:
                    st.markdown(f"- _{x}_")

            st.markdown(f"#### {t['todo']}")
            steps = ren["next_steps"] if use_ren else [f"**{s['action']}** - {s['why']}" for s in ans["next_steps"]]
            for i, s in enumerate(steps, 1):
                st.markdown(f"{i}. {s}")
            qs = ren["questions_for_user"] if use_ren else ans["questions_for_user"]
            if qs:
                st.markdown(f"#### {t['ask']}")
                for q in qs:
                    st.markdown(f"- {q}")
            if ans["not_covered"]:
                st.markdown(f"#### {t['cant']}")
                for n in ans["not_covered"]:
                    st.markdown(f"- {n}")
        elif a["scope_message"] and a["triage"]["severity"] == "none":
            st.info(a["scope_message"])

        if a["draft"]:
            st.subheader(t["s4"])
            st.caption(t["letter_cap"])
            letter = st.text_area("Edit freely", a["draft"], height=360)
            st.download_button("⬇️ Download letter", letter, file_name="demand_letter.txt")
            if a.get("draft_notes"):
                with st.expander("📮 How to send it"):
                    st.text(a["draft_notes"])

        with st.expander("🔍 Law check report (what was checked, and what wasn't)"):
            st.markdown(f"**Sections cited:** {', '.join(a['cited']) or 'none'}")
            st.markdown(f"**Problems caught in the first draft:** {len(a['violations_first_try'])}")
            for v in a["violations_first_try"]:
                st.markdown(f"- `{v['kind']}` {v['detail']}")
            if a["violations_final"]:
                st.markdown("**Still failing after one retry, so removed:**")
                for v in a["violations_final"]:
                    st.markdown(f"- `{v['kind']}` {v['detail']}")
            if a["support"]:
                st.markdown("**Support check:** " + ", ".join(f"{s['cite']}: {'yes' if s['supported'] else 'unsure' if s['supported'] is None else 'no'}" for s in a["support"]))
            st.markdown("**Escalation reasons:** " + ("; ".join(a["escalation_reasons"]) or "none"))
            st.caption(f"Versions: {json.dumps(a.get('versions', {}))}")
        if st.button(t["change"]):
            ss.stage = "confirm"
            st.rerun()

# ======================= practice =======================
with tab_practice:
    st.subheader("Practise the hard conversation")
    st.caption("I play the other side (HR, the seller's support agent, or your landlord). Then I give you tips. This is rehearsal - nothing here is legal advice.")
    role = st.selectbox("Who do you want to practise with?", ["HR manager at my company", "customer-support agent of the seller", "my landlord"])
    language = st.selectbox("Language", ["Hinglish", "English", "Hindi"])
    summary = ss.advice["answer"]["summary"] if ss.advice and ss.advice.get("answer") else (ss.story or "a money dispute")
    for m in ss.practice:
        st.chat_message("user" if m["role"] == "user" else "assistant", avatar="🧑" if m["role"] == "user" else "🎭").write(m["content"])
    said = st.chat_input("Say something to them...", max_chars=500)
    if said and budget_ok(1):
        ss.practice.append({"role": "user", "content": said})
        sys = prompts.PRACTICE.format(role=role, summary=summary[:800], language=language)
        reply = run_safely(get_llm().chat, [{"role": "system", "content": sys}] + ss.practice[-12:], "practice")
        if reply:
            ss.practice.append({"role": "assistant", "content": reply.strip()})
        st.rerun()
    if ss.practice and st.button("🧑‍🏫 How did I do?") and budget_ok(1):
        convo = "\n".join(f"{'USER' if m['role'] == 'user' else role.upper()}: {m['content']}" for m in ss.practice)
        tips = run_safely(get_llm().chat, [{"role": "system", "content": prompts.COACH.format(role=role, language=language)}, {"role": "user", "content": convo}], "coach")
        if tips:
            st.success(tips)

# ======================= how it works =======================
with tab_how:
    st.markdown("""
**What I cover:** unpaid salary (Code on Wages, 2019, in force since 21 Nov 2025) and consumer refunds (Consumer Protection Act, 2019).
Rental deposits only partly: State rent laws are *not* verified here, so I won't quote them.
**What I don't cover:** criminal cases, family or property matters, cheque bounce, freelance invoices, or anything where you are being sued. For these I point you to free legal aid.

**Why I don't invent laws**
1. I can only cite from a short list of provisions copied by a script from the official Gazette (a few from a legal website, marked as such), each with its source and date.
2. Code rejects any section, Act, time limit or percentage that isn't in that list or in my own calculations, any court case, and any old law (like the Payment of Wages Act or IPC 420) unless I'm correcting it.
3. Dates, deadlines, fees and where to file are calculated by code, not by the AI.
4. If my answer still fails the check, the failing sentences are removed and you're told to talk to a person. Sentences a second check can't confirm are shown separately.

**What a person does:** you confirm the facts before anything is calculated; you edit, sign and send any letter. For risky situations (violence, a child, scams, arrest, close deadlines, government on the other side) I send you to a person.
""")
    st.caption(f"Law list as of {corpus.load()['as_of']}. Not reviewed by a lawyer yet.")
