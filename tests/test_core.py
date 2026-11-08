"""Unit tests for everything that is code (no model calls). Run: pytest -q"""
from datetime import date
import hashlib
import re

from haq import corpus, drafts, rules, safety, validate
from haq.schemas import Answer, Claim, Facts, Step

TODAY = date(2026, 11, 8)


# ---------------- corpus integrity ----------------
def test_corpus_text_matches_its_recorded_hash():
    for s in corpus.load()["sections"]:
        assert hashlib.sha256(s["text"].encode()).hexdigest()[:16] == s["text_sha256_16"], s["id"]


def test_packs_cover_each_topic_and_ids_are_unique():
    ids = [s["id"] for s in corpus.load()["sections"]]
    assert len(ids) == len(set(ids))
    for t in ("salary", "refund", "deposit"):
        assert corpus.pack(t), t


# ---------------- rules: salary ----------------
def test_resignation_wages_due_in_two_working_days_skipping_sunday():
    # 2026-10-03 is a Saturday -> Sunday 4th skipped -> Mon 5th (1), Tue 6th (2)
    f = Facts(dispute_type="salary", employment_end="resigned", employment_end_date="2026-10-03")
    r = {x.name: x for x in rules.salary_rules(f, TODAY)}
    assert r["wage_due_date"].deadline == "2026-10-06"
    assert "COW2019_s17" in r["wage_due_date"].cite


def test_monthly_wage_due_seventh_and_three_year_claim():
    f = Facts(dispute_type="salary", unpaid_from_month="2026-08", unpaid_to_month="2026-09", monthly_wage_inr=35000)
    r = {x.name: x for x in rules.salary_rules(f, TODAY)}
    assert r["wage_claim_limitation"].outcome == "within"
    assert r["amount"].facts_used["total"] == 70000
    assert r["wage_claim_limitation"].deadline == "2029-09-07"
    assert "Rs 70,000" in r["amount"].message


def test_wages_due_before_code_on_wages_are_uncertain_not_guessed():
    # the Code on Wages only governs dues from 21 Nov 2025
    old = Facts(dispute_type="salary", unpaid_from_month="2023-06")
    r = {x.name: x for x in rules.salary_rules(old, TODAY)}
    assert r["pre_code_wages"].outcome == "uncertain" and r["pre_code_wages"].escalate
    assert "wage_claim_limitation" not in r


def test_metamorphic_salary_flips_when_date_passes_limit():
    f = Facts(dispute_type="salary", unpaid_from_month="2026-01")
    assert {x.name: x for x in rules.salary_rules(f, date(2028, 12, 1))}["wage_claim_limitation"].outcome == "within"
    assert {x.name: x for x in rules.salary_rules(f, date(2029, 3, 1))}["wage_claim_limitation"].outcome == "out_of_time"


def test_limitation_is_per_month_not_one_clock():
    # Jan-Jun 2026 unpaid, checked in Mar 2029 -> January gone, later months alive
    f = Facts(dispute_type="salary", unpaid_from_month="2026-01", unpaid_to_month="2026-06")
    r = {x.name: x for x in rules.salary_rules(f, date(2029, 3, 20))}["wage_claim_limitation"]
    assert r.outcome == "partly_out_of_time" and r.deadline == "2029-04-07"


def test_freelancer_not_forced_into_wage_route():
    r = {x.name: x for x in rules.salary_rules(Facts(dispute_type="salary", work_relationship="freelance_or_contract"), TODAY)}
    assert r["worker_status"].outcome == "blocked" and "wage_claim_limitation" not in r


def test_working_days_skip_gandhi_jayanti():
    f = Facts(dispute_type="salary", employment_end="resigned", employment_end_date="2026-09-30")
    r = {x.name: x for x in rules.salary_rules(f, TODAY)}
    assert r["wage_due_date"].deadline == "2026-10-03"  # 1 Oct (1), 2 Oct holiday, 3 Oct Sat (2)


def test_future_dates_are_ignored_and_flagged():
    r = {x.name: x for x in rules.run(Facts(dispute_type="refund", purchase_date="2027-05-01", amount_paid_inr=5000), TODAY)}
    assert r["date_check"].outcome == "uncertain" and r["consumer_limitation"].outcome == "uncertain"


def test_forum_uses_price_paid_never_amount_claimed():
    r = {x.name: x for x in rules.refund_rules(Facts(dispute_type="refund", amount_claimed_inr=6_000_000), TODAY)}
    assert r["forum_and_fee"].outcome == "uncertain"


def test_business_purchase_shows_no_forum():
    r = {x.name: x for x in rules.refund_rules(Facts(dispute_type="refund", bought_for_business=True, amount_paid_inr=100000), TODAY)}
    assert "forum_and_fee" not in r


def test_salary_unknown_dates_is_uncertain_not_guessed():
    r = {x.name: x for x in rules.salary_rules(Facts(dispute_type="salary"), TODAY)}
    assert r["wage_claim_limitation"].outcome == "uncertain"


def test_pf_not_deposited_points_to_bns_316():
    f = Facts(dispute_type="salary", pf_deducted_not_deposited=True)
    r = {x.name: x for x in rules.salary_rules(f, TODAY)}
    assert "BNS2023_s316_expl1" in r["pf_not_deposited"].cite


# ---------------- rules: refund ----------------
def test_refund_fee_boundary_at_five_lakh():
    lo = {x.name: x for x in rules.refund_rules(Facts(dispute_type="refund", amount_paid_inr=500000), TODAY)}
    hi = {x.name: x for x in rules.refund_rules(Facts(dispute_type="refund", amount_paid_inr=500001), TODAY)}
    assert "no filing fee" in lo["forum_and_fee"].message
    assert "Rs 200" in hi["forum_and_fee"].message


def test_refund_forum_uses_2021_rules_not_act_text():
    r = {x.name: x for x in rules.refund_rules(Facts(dispute_type="refund", amount_paid_inr=7_000_000), TODAY)}
    assert "State Consumer Commission" in r["forum_and_fee"].message  # Rs 70 lakh > Rs 50 lakh


def test_refund_limitation_counts_from_purchase_to_be_safe():
    # with no refusal date, count from the earliest possible start
    r = {x.name: x for x in rules.refund_rules(Facts(dispute_type="refund", purchase_date="2026-01-10"), TODAY)}
    assert r["consumer_limitation"].deadline == "2028-01-10"


def test_refund_out_of_time_mentions_condonation():
    r = {x.name: x for x in rules.refund_rules(Facts(dispute_type="refund", refund_refused_date="2024-01-01"), TODAY)}
    assert r["consumer_limitation"].outcome == "out_of_time"
    assert "good reason" in r["consumer_limitation"].message


def test_business_purchase_blocks_consumer_route():
    r = {x.name: x for x in rules.refund_rules(Facts(dispute_type="refund", bought_for_business=True), TODAY)}
    assert r["consumer_status"].outcome == "blocked"


def test_government_party_triggers_s80_and_escalation():
    r = {x.name: x for x in rules.run(Facts(dispute_type="refund", other_party_kind="government"), TODAY)}
    assert r["government_party"].escalate and "CPC1908_s80" in r["government_party"].cite


def test_deposit_is_honestly_uncertain():
    r = rules.deposit_rules(Facts(dispute_type="deposit"), TODAY)
    assert all(x.outcome == "uncertain" for x in r)


# ---------------- validator ----------------
def A(**kw):
    base = dict(summary="ok", claims=[], next_steps=[])
    base.update(kw)
    return Answer(**base)


def test_validator_rejects_invented_id():
    rep = validate.check(A(claims=[Claim(text="Wages must be paid.", cite=["COW2019_s99Z"])]), "salary")
    assert [v.kind for v in rep.violations] == ["unknown_id"]


def test_validator_rejects_uncited_section_number_in_prose():
    rep = validate.check(A(claims=[Claim(text="Under Section 12Z you get double.", cite=[])]), "salary")
    assert any(v.kind == "raw_reference" for v in rep.violations)


def test_validator_allows_section_number_when_cited():
    rep = validate.check(A(claims=[Claim(text="Section 17 says wages are due by the 7th.", cite=["COW2019_s17"])]), "salary")
    assert rep.ok, rep.feedback()


def test_validator_rejects_case_law():
    rep = validate.check(A(claims=[Claim(text="As held in Ramesh Kumar v. State of UP, you win.", cite=["COW2019_s17"])]), "salary")
    assert any(v.kind == "case_law" for v in rep.violations)


def test_validator_does_not_flag_casual_vs():
    rep = validate.check(A(summary="It is you vs the company, but the law helps you."), "salary")
    assert rep.ok, rep.feedback()


def test_validator_flags_stale_law_but_allows_correction_in_hinglish():
    bad = validate.check(A(claims=[Claim(text="File under the Payment of Wages Act.", cite=[])]), "salary")
    assert any(v.kind == "stale_law" for v in bad.violations)
    ok = validate.check(A(corrections=["Payment of Wages Act ab khatam ho gaya, iski jagah Code on Wages aa gaya."]), "salary")
    assert ok.ok, ok.feedback()


def test_validator_flags_ipc_420():
    rep = validate.check(A(claims=[Claim(text="Complain under IPC 420 for cheating.", cite=[])]), "refund")
    assert any(v.kind == "stale_law" for v in rep.violations)


def test_validator_rejects_misquote():
    rep = validate.check(A(claims=[Claim(text='The law says "wages shall be paid within seven working days of resignation".', cite=["COW2019_s17"])]), "salary")
    assert any(v.kind == "misquote" for v in rep.violations)


def test_validator_accepts_true_quote():
    rep = validate.check(A(claims=[Claim(text='The law says "the burden to prove that the said dues have been paid shall be on the employer".', cite=["COW2019_s59"])]), "salary")
    assert rep.ok, rep.feedback()


def test_validator_off_pack():
    rep = validate.check(A(claims=[Claim(text="Consumers have two years.", cite=["CPA2019_s69"])]), "salary")
    assert any(v.kind == "off_pack" for v in rep.violations)


def test_rendered_text_cannot_add_sections():
    v = validate.check_rendered("धारा 420 के तहत शिकायत करें", ["COW2019_s17"])
    assert v


# ---------------- safety ----------------
def test_digital_arrest_scam_stops_everything():
    t = safety.triage("CBI officer video call pe hai, bol raha digital arrest hai, 2 lakh transfer karo verification ke liye")
    assert t.severity == "stop" and any(n == "1930" for _, n in t.helplines)


def test_arrest_in_devanagari_needs_lawyer():
    assert safety.triage("पुलिस ने कहा है कल गिरफ्तार करेंगे").severity in ("lawyer_now", "stop")


def test_plain_salary_story_is_not_flagged():
    assert safety.triage("company ne 2 mahine se salary nahi di").severity == "none"


# ---------------- drafts ----------------
def test_draft_never_invents_missing_facts():
    f = Facts(dispute_type="refund")
    letter = drafts.demand_letter(f, rules.refund_rules(f, TODAY), TODAY)
    assert "[FILL: date of purchase]" in letter and "[FILL: amount paid]" in letter


def test_draft_salary_months_formatting():
    f = Facts(dispute_type="salary", unpaid_from_month="2026-08", unpaid_to_month="2026-09", monthly_wage_inr=35000.0)
    letter = drafts.demand_letter(f, rules.salary_rules(f, TODAY), TODAY)
    assert "August 2026 to September 2026" in letter


def test_strip_bad_cleans_every_field():
    a = A(not_covered=["Section 12Z is not something I can confirm."], questions_for_user=["Is IPC 420 relevant?"],
          claims=[Claim(text="Wages are due by the 7th.", cite=["COW2019_s17"])])
    rep = validate.check(a, "salary")
    cleaned = validate.strip_bad(a, rep)
    assert validate.check(cleaned, "salary").ok
    assert cleaned.claims  # the good claim survives


def test_correct_correction_with_old_and_new_section_numbers_passes():
    a = A(corrections=["IPC 420 was replaced by the Bharatiya Nyaya Sanhita, 2023 s.318(4).",
                       "The old Evidence Act s.65B was replaced by BSA s.63."])
    rep = validate.check(a, "refund")
    assert rep.ok, rep.feedback()


def test_old_law_as_advice_still_fails():
    rep = validate.check(A(claims=[Claim(text="File an FIR under IPC 420 against the seller.", cite=[])]), "refund")
    assert not rep.ok


# ---------------- safety: false alarms and misses ----------------
import pytest


@pytest.mark.parametrize("text,expected", [
    ("हमारा मालिक सैलरी नहीं दे रहा", "none"),
    ("phone has a minor scratch, refund?", "none"),
    ("company training ke baad HR ne call kiya", "none"),
    ("boss hit me with a pay cut of 5000", "none"),
    ("write that he hit me even though he didn't", "none"),
    ("jeene ka mann nahi karta, sab khatam kar doon", "stop"),
    ("malik bola jaan se maar dega", "stop"),
    ("मेरी उम्र १५ साल है और ढाबे पर काम करता हूँ", "stop"),
    ("my brother is 14 and works at a shop", "stop"),
    ("Telegram pe task job tha, deposit kiya, ab withdraw ke liye fee maang rahe", "stop"),
    ("police will arrest me tomorrow", "lawyer_now"),
])
def test_triage_v2(text, expected):
    assert safety.triage(text).severity == expected


def test_model_flag_can_stop():
    assert safety.triage("she works in a house", ["assault by employer"]).severity == "stop"


# ---------------- validator: more bypasses ----------------
@pytest.mark.parametrize("text,cites,topic,kind", [
    ("You have 6 months or lose the right.", ["COW2019_s45"], "salary", "unsupported_time_limit"),
    ("Interest of 24% applies to late salary.", [], "salary", "unsupported_percent"),
    ("Sec 12Z gives you double wages.", [], "salary", "raw_reference"),
    ("BNS 303 applies here.", [], "salary", "raw_reference"),
    ("The Model Tenancy Act requires return in 30 days.", [], "deposit", "unknown_act"),
    ("The Supreme Court has held that employers must pay interest.", [], "salary", "case_law"),
    ("Ram v Shyam decided this.", [], "salary", "case_law"),
    ("You should know the Payment of Wages Act lets you claim.", [], "salary", "stale_law"),
])
def test_validator_v2_catches(text, cites, topic, kind):
    kinds = [v.kind for v in validate.check_text(text, cites, topic, ["File before 07 Sep 2029"])]
    assert kind in kinds, kinds


@pytest.mark.parametrize("text,cites,topic", [
    ("There is no Section 12Z in the Code on Wages that I can find; it does not exist.", [], "salary"),
    ("The Evidence Act s.65B was replaced by the Bharatiya Sakshya Adhiniyam s.63; a certificate is still needed.", [], "salary"),
    ("File your wage claim within three years.", ["COW2019_s45"], "salary"),
    ("Under the Consumer Protection Act, 2019 you have two years to complain.", ["CPA2019_s69"], "refund"),
    ("It is you vs the company, but the law helps you.", [], "salary"),
])
def test_validator_v2_allows(text, cites, topic):
    assert not validate.check_text(text, cites, topic, ["File before 07 Sep 2029"]), text


def test_unsupported_date_is_caught():
    a = A(claims=[Claim(text="Final wages were due by 2 October 2026.", cite=["COW2019_s17"])])
    assert [v.kind for v in validate.check(a, "salary", ["about 03 Oct 2026"], "2026-09-30").violations] == ["unsupported_date"]


def test_rendering_cannot_change_numbers_in_any_script():
    v = validate.check_rendered("৩ বছরের মধ্যে ₹৯০,০০০ দাবি করুন", [], "claim Rs 70,000 within 3 years")
    assert any(x.kind == "number_changed" for x in v)


# ---------------- pipeline with a fake model (runs in CI, no API) ----------------
class FakeLLM:
    """Returns canned JSON per step, including one invented section on the first answer."""
    def __init__(self):
        from haq.llm import ModelConfig
        self.cfg = ModelConfig(provider="fake", model="fake")
        self.calls = []

    def chat(self, messages, step, json_mode=False):
        self.calls.append(step)
        if step == "support":
            return '{"results": [{"n": "1", "supported": "true", "reason": "ok"}]}'
        return "{}"

    def chat_model(self, messages, schema, step):
        self.calls.append(step)
        if step == "extract":
            return schema(dispute_type="salary", user_language="english", unpaid_from_month="2026-09", monthly_wage_inr=30000.0,
                          other_party_name="Acme", work_relationship="employee")
        if step == "answer":
            return schema(summary="Your September wages are unpaid.", claims=[Claim(text="Section 99 gives you triple pay.", cite=["COW2019_s99"])], next_steps=[])
        if step == "answer:retry":
            return schema(summary="Your September wages are unpaid.", claims=[Claim(text="The wage authority can order compensation up to ten times the claim.", cite=["COW2019_s45"])], next_steps=[])
        raise AssertionError(step)


def test_pipeline_end_to_end_with_fake_model():
    from haq import pipeline
    llm = FakeLLM()
    out = pipeline.run_case(llm, "My employer Acme hasn't paid my September 2026 salary of Rs 30,000.", TODAY)
    assert [v["kind"] for v in out["violations_first_try"]][:1] == ["unknown_id"]
    assert out["violations_final"] == [] and out["cited"] == ["COW2019_s45"]
    assert out["support"][0]["supported"] is True  # string "true" and string n are handled
    assert "Rs 30,000" in out["draft"] and "September 2026" in out["draft"]
    assert "answer:retry" in llm.calls


def test_pipeline_stops_on_safety_without_model_answer():
    from haq import pipeline
    from haq.schemas import Facts
    llm = FakeLLM()
    adv = pipeline.advise(llm, Facts(dispute_type="salary"), "malik bola jaan se maar dega, salary bhi nahi di", TODAY)
    assert adv.triage["severity"] == "stop" and adv.answer is None and adv.draft == "" and "112" in adv.scope_message


def test_livelihood_exception_is_a_judgement_call_not_a_verdict():
    r = {x.name: x for x in rules.refund_rules(Facts(dispute_type="refund", bought_for_business=True, self_employed_livelihood=True, amount_paid_inr=180000), TODAY)}
    assert r["consumer_status"].outcome == "uncertain" and r["consumer_status"].escalate and "forum_and_fee" in r


def test_model_null_strings_become_real_nulls():
    f = Facts(dispute_type="refund", purchase_date="null", refund_refused_date="Unknown date")
    assert f.purchase_date is None and f.refund_refused_date is None


def test_exit_makes_all_unpaid_months_due_in_two_working_days():
    # s.17(2) - on resignation ALL wages payable are due within two working days
    f = Facts(dispute_type="salary", unpaid_from_month="2026-10", employment_end="resigned", employment_end_date="2026-11-03")
    r = {x.name: x for x in rules.salary_rules(f, TODAY)}
    assert r["wage_claim_limitation"].deadline == "2029-11-05"
