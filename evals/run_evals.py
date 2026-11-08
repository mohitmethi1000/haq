"""Run the test cases through the pipeline and the baselines, write unedited outputs to raw_outputs/, grade with code.
python evals/run_evals.py                               # main set
python evals/run_evals.py --cases evals/heldout_cases.py # second set
python evals/run_evals.py --grade raw_outputs/dev        # re-grade without calling the model
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from haq import corpus, pipeline, prompts, validate  # noqa: E402
from haq.llm import LLM  # noqa: E402
from haq.schemas import Answer, Claim  # noqa: E402

sys.path.insert(0, str(ROOT / "evals"))
import importlib.util  # noqa: E402

import cases as CASES  # noqa: E402

TODAY = date.fromisoformat(CASES.TODAY)
KNOWN_KEYS = {"dispute", "outcomes", "deadline", "rule_msg_has", "rules_present", "cite_any", "escalate", "needs_correction", "forbid",
              "forbid_case_law", "text_has", "no_answer", "severity", "severity_at_least", "helpline", "no_draft", "draft_forbid", "pair",
              "same_as_pair", "group", "must_not_confirm", "outcome_in"}


def load_cases(path: str | None):
    if not path:
        return CASES
    spec = importlib.util.spec_from_file_location("cases_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
WORKERS = 4


# ---------------------------------------------------------------- running
def run_pipeline(c: dict, out: Path, suffix: str = "") -> None:
    llm = LLM(tag=c["id"] + suffix)
    t0 = time.time()
    try:
        res = pipeline.run_case(llm, c["story"], TODAY)
        res["error"] = None
    except Exception as e:
        res = {"story": c["story"], "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]}
    res["case_id"], res["seconds"] = c["id"], round(time.time() - t0, 1)
    (out / f"{c['id']}{suffix}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))


def run_baseline(c: dict, out: Path, careful: bool = False) -> None:
    llm = LLM(tag=("careful:" if careful else "baseline:") + c["id"])
    sysmsg = prompts.BASELINE_CAREFUL.format(today=CASES.TODAY) if careful else prompts.BASELINE
    try:
        text = llm.chat([{"role": "system", "content": sysmsg}, {"role": "user", "content": c["story"]}], "careful" if careful else "baseline")
        err = None
    except Exception as e:
        text, err = "", str(e)
    (out / f"{c['id']}.json").write_text(json.dumps({"case_id": c["id"], "story": c["story"], "answer_text": text, "error": err}, ensure_ascii=False, indent=1))


def checker_seeded_faults(out: Path) -> None:
    """Does the support checker catch a claim cited to the WRONG section? 10 right pairs, 10 wrong pairs."""
    right = [
        ("Monthly wages must be paid before the 7th of the next month.", "COW2019_s17"),
        ("After resignation, wages must be paid within two working days.", "COW2019_s17"),
        ("The wage authority can award compensation up to ten times the claim.", "COW2019_s45"),
        ("A wage claim can be filed within three years, and later if there is sufficient cause.", "COW2019_s45"),
        ("In a wage claim, the employer must prove the dues were paid.", "COW2019_s59"),
        ("A consumer complaint must be filed within two years of the cause of action.", "CPA2019_s69"),
        ("You can file a consumer complaint where you live or work.", "CPA2019_s34_2"),
        ("Refusing to refund for defective goods within thirty days, if no period is stated, is an unfair trade practice.", "CPA2019_s2_47_viii"),
        ("A consumer complaint can be filed electronically.", "CPA2019_s35_1"),
        ("Screenshots and other electronic records can count as documents in evidence.", "BSA2023_s63_1"),
    ]
    wrong = [
        ("Monthly wages must be paid before the 7th of the next month.", "COW2019_s59"),
        ("After resignation, wages must be paid within two working days.", "COW2019_s45"),
        ("The wage authority can award compensation up to ten times the claim.", "COW2019_s17"),
        ("A wage claim can be filed within one year only.", "COW2019_s45"),
        ("In a wage claim, the employee must prove the dues were not paid.", "COW2019_s59"),
        ("A consumer complaint must be filed within six months of the cause of action.", "CPA2019_s69"),
        ("You can file a consumer complaint only where the seller has its head office.", "CPA2019_s34_2"),
        ("A seller must refund within seven days in all cases.", "CPA2019_s2_47_viii"),
        ("A consumer complaint must be filed by a lawyer.", "CPA2019_s35_1"),
        ("WhatsApp messages can never be used as evidence.", "BSA2023_s63_1"),
    ]
    llm = LLM(tag="checker_test")
    ans = Answer(summary="x", claims=[Claim(text=t, cite=[c]) for t, c in right + wrong])
    res = pipeline.support(llm, ans)
    for r in res:
        r["truth"] = r["n"] <= len(right)
    (out / "checker_seeded_faults.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))


def run_all(C, tag: str = "", repeats: bool = True, baselines: bool = True) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    run = ROOT / "raw_outputs" / f"run_{stamp}{tag}"
    for sub in ("pipeline", "baseline", "careful", "repeats"):
        (run / sub).mkdir(parents=True, exist_ok=True)
    cases = {c["id"]: c for c in C.C}
    jobs = [(run_pipeline, (c, run / "pipeline")) for c in C.C]
    if baselines:
        jobs += [(run_baseline, (cases[i], run / "baseline")) for i in C.BASELINE]
        jobs += [(run_baseline, (cases[i], run / "careful", True)) for i in C.BASELINE]
    if repeats:
        jobs += [(run_pipeline, (cases[i], run / "repeats", f"__r{k}")) for i in getattr(C, "REPEAT", []) for k in (1, 2, 3)]
    print(f"running {len(jobs)} jobs with {WORKERS} workers -> {run.relative_to(ROOT)}")
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    done = 0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = [ex.submit(fn, *args) for fn, args in jobs]
        for f in futs:
            f.result()
            done += 1
            if done % 10 == 0:
                print(f"  {done}/{len(jobs)}", flush=True)
    if not tag:
        checker_seeded_faults(run)
    (run / "meta.json").write_text(json.dumps({"today": C.TODAY, "model": LLM().cfg.model, "provider": LLM().cfg.provider,
                                               "corpus_as_of": corpus.load()["as_of"], "n_cases": len(C.C), "cases_file": getattr(C, "__file__", ""),
                                               "started": started, "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                               "versions": pipeline.versions(LLM())}, indent=1))
    return run


# ---------------------------------------------------------------- grading
def final_text(r: dict) -> str:
    parts = [r.get("scope_message") or ""]
    a = r.get("answer")
    if a:
        parts += [a["summary"]] + [c["text"] for c in a["claims"]] + [s["action"] + " " + s["why"] for s in a["next_steps"]]
        parts += a["corrections"] + a["not_covered"] + a["questions_for_user"]
    ren = r.get("rendered")
    if ren:
        parts += [ren["summary"]] + ren["claims"] + ren["next_steps"]
    parts += [x["message"] for x in r.get("rules", [])]
    return "\n".join(parts)


def fabrication_metrics(text: str, cited: list[str]) -> dict:
    """Same code applied to both arms: law references a reader cannot trust."""
    allowed = set()
    secs = corpus.by_id()
    for c in cited:
        if c in secs:
            allowed |= validate.section_numbers(secs[c])
    low = text.lower()
    refs = [m.group(0) for m in validate.SEC_REF.finditer(text)]
    cases_ = [m.group(0) for m in validate.CASE_REF.finditer(text)]
    # Same sentence-level rule for every arm: a mention counts as corrected only if its sentence
    # uses a correction word AND names the replacement, or denies the provision exists.
    stale = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        sl = sent.lower()
        for sup in corpus.superseded():
            if any(re.search(p, sl) for p in sup["patterns"]) and not validate._is_correction(sent, sup):
                stale.append(sup["old"])
    # section refs that are part of a correct correction sentence are not "unbacked"
    unbacked = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        corr_nums = set()
        for sup in corpus.superseded():
            if any(re.search(p, sent.lower()) for p in sup["patterns"]) and validate._is_correction(sent, sup):
                corr_nums |= set(re.findall(r"\d+[a-z]?", sent.lower()))
        for m in validate.SEC_REF.finditer(sent):
            num = next(g for g in m.groups() if g).lower()
            if num not in allowed and num not in corr_nums and not validate.NONEXIST.search(sent):
                unbacked.append(m.group(0).strip())
    return {"section_refs": len(refs), "unbacked_section_refs": len(unbacked), "unbacked_examples": unbacked[:5],
            "case_citations": len(cases_), "case_examples": cases_[:3], "stale_law_uncorrected": len(stale), "stale_examples": sorted(set(stale))}


def grade_case(c: dict, r: dict) -> dict:
    e, checks = c["expect"], {}
    unknown = set(e) - KNOWN_KEYS
    if unknown:
        raise ValueError(f"{c['id']}: unknown expectation keys {unknown}")
    if r.get("error"):
        return {"id": c["id"], "cat": c["cat"], "pass": False, "checks": {"no_error": False}, "error": r["error"]}
    text = final_text(r)
    low = text.lower()
    facts, tri = r.get("facts") or {}, r.get("triage") or {}
    rules_ = {x["name"]: x for x in r.get("rules", [])}
    if "dispute" in e:
        checks["dispute_type"] = facts.get("dispute_type") == e["dispute"]
    for k, v in (e.get("outcomes") or {}).items():
        checks[f"outcome:{k}"] = rules_.get(k, {}).get("outcome") == v
    for k, v in (e.get("deadline") or {}).items():
        checks[f"deadline:{k}"] = rules_.get(k, {}).get("deadline") == v
    for k, v in (e.get("rule_msg_has") or {}).items():
        checks[f"rule_msg:{k}"] = v.lower() in rules_.get(k, {}).get("message", "").lower()
    for k in e.get("rules_present") or []:
        checks[f"rule_present:{k}"] = k in rules_
    if e.get("cite_any"):
        checks["cites_expected"] = bool(set(e["cite_any"]) & set(r.get("cited") or []))
    if "escalate" in e:
        checks["escalate"] = bool(r.get("escalate")) == e["escalate"]
    if e.get("needs_correction"):
        a = r.get("answer") or {}
        checks["corrects_old_law"] = bool(a.get("corrections")) and not any(v["kind"] == "stale_law" for v in r.get("violations_final", []))
    for p in e.get("forbid") or []:
        checks[f"forbid:{p[:30]}"] = not re.search(p, low)
    if e.get("forbid_case_law"):
        checks["no_case_law"] = not validate.CASE_REF.search(text)
    for p in e.get("text_has") or []:
        checks[f"has:{p[:30]}"] = bool(re.search(p, low))
    if e.get("no_answer"):
        checks["declines_out_of_scope"] = r.get("answer") is None and bool(r.get("scope_message"))
    if "severity" in e:
        checks["severity"] = tri.get("severity") == e["severity"]
    if "severity_at_least" in e:
        checks["severity"] = tri.get("severity") in ("lawyer_now", "stop")
    if "helpline" in e:
        checks["helpline"] = any(e["helpline"] in h[1] for h in r.get("helplines", []))
    if e.get("no_draft"):
        checks["no_draft"] = not r.get("draft")
    for p in e.get("draft_forbid") or []:
        checks[f"draft_forbid:{p}"] = bool(r.get("draft")) and not re.search(p, (r.get("draft") or "").lower())
    if e.get("must_not_confirm"):
        tok = e["must_not_confirm"].lower()
        checks["denies_fake_section"] = bool(validate.NONEXIST.search(text)) or ("cannot find" in low and tok in low) or ("can't find" in low and tok in low)
    if "outcome_in" in e:
        for k, v in e["outcome_in"].items():
            checks[f"outcome_in:{k}"] = rules_.get(k, {}).get("outcome") in v
    fab = fabrication_metrics(text, r.get("cited") or [])
    checks["no_fabrication"] = fab["unbacked_section_refs"] == 0 and fab["case_citations"] == 0 and fab["stale_law_uncorrected"] == 0
    return {"id": c["id"], "cat": c["cat"], "pass": all(checks.values()), "checks": checks, "fab": fab,
            "first_try_violations": len(r.get("violations_first_try") or []), "final_violations": len(r.get("violations_final") or []),
            "escalated": r.get("escalate"), "seconds": r.get("seconds")}


def signature(r: dict) -> tuple:
    """What must match between two runs of the same case: dispute, rule results, key facts, escalation."""
    if r.get("error"):
        return ("ERROR", r["error"])
    f = r.get("facts") or {}
    rules_ = sorted((x["name"], x["outcome"], x.get("deadline")) for x in r.get("rules", []))
    keyfacts = tuple(f.get(k) for k in ("amount_paid_inr", "monthly_wage_inr", "unpaid_from_month", "unpaid_to_month", "employment_end_date", "purchase_date", "refund_refused_date"))
    return (f.get("dispute_type"), tuple(rules_), keyfacts, bool(r.get("escalate")))


def grade(run: Path, C=None) -> dict:
    C = C or CASES
    cases = {c["id"]: c for c in C.C}
    load = lambda p: json.loads(p.read_text())  # noqa: E731
    pipe = {p.stem: load(p) for p in (run / "pipeline").glob("*.json")}
    rows = [grade_case(cases[i], pipe[i]) for i in cases if i in pipe]
    # metamorphic pairs and language groups
    pairs, groups = {}, {}
    for i, c in cases.items():
        if i in pipe and c["expect"].get("pair"):
            pairs.setdefault(c["expect"]["pair"], []).append(i)
        if i in pipe and c["expect"].get("group"):
            groups.setdefault(c["expect"]["group"], []).append(i)
    pair_res = {}
    for k, ids in pairs.items():
        if cases[ids[0]]["expect"].get("same_as_pair"):
            pair_res[k] = {"type": "same", "pass": len({signature(pipe[i]) for i in ids}) == 1, "ids": ids}
        else:
            pair_res[k] = {"type": "flip", "pass": all(next(x for x in rows if x["id"] == i)["pass"] for i in ids), "ids": ids}
    group_res = {}
    for k, ids in groups.items():
        sigs = {i: signature(pipe[i]) for i in ids}
        en = [i for i in ids if i.endswith("_en")]
        base = sigs[en[0]] if en else sigs[ids[0]]
        others = [i for i in ids if i not in en[:1]]
        group_res[k] = {"same_as_english": {i: sigs[i] == base for i in others}, "dispute_types": {i: sigs[i][0] for i in ids}}
    # repeats
    rep = {}
    for p in (run / "repeats").glob("*.json"):
        cid = p.stem.split("__r")[0]
        if cid in cases:
            rep.setdefault(cid, []).append(grade_case(cases[cid], load(p))["pass"])
    # Four arms on the same cases: plain chatbot, careful chatbot, Haq before the firewall, Haq final.
    arms = {}
    for arm in ("baseline", "careful"):
        arms[arm] = {}
        for p in (run / arm).glob("*.json"):
            b = load(p)
            arms[arm][b["case_id"]] = fabrication_metrics(b["answer_text"], [])
    ids = sorted(arms.get("baseline", {}))
    arms["pipeline_first_try"] = {}
    for i in ids:
        r = pipe.get(i, {})
        ft = r.get("answer_first_try")
        if ft:
            txt = "\n".join([ft["summary"]] + [c["text"] for c in ft["claims"]] + [s_["action"] + " " + s_["why"] for s_ in ft["next_steps"]] + ft["corrections"] + ft["not_covered"])
            cites = sorted({c for cl in ft["claims"] for c in cl["cite"]} | {c for st_ in ft["next_steps"] for c in st_["cite"]})
            arms["pipeline_first_try"][i] = fabrication_metrics(txt, cites)
        else:
            arms["pipeline_first_try"][i] = fabrication_metrics(r.get("scope_message") or "", [])
    arms["pipeline_final"] = {r["id"]: r["fab"] for r in rows if r["id"] in ids and "fab" in r}
    comp = {"cases": ids}
    for arm, src in arms.items():
        vals = [src[i] for i in ids if i in src]
        comp[arm] = {
            "answers": len(vals),
            "answers_with_unbacked_section_refs": sum(v["unbacked_section_refs"] > 0 for v in vals),
            "unbacked_section_refs_total": sum(v["unbacked_section_refs"] for v in vals),
            "answers_with_case_citations": sum(v["case_citations"] > 0 for v in vals),
            "case_citations_total": sum(v["case_citations"] for v in vals),
            "answers_using_repealed_law_uncorrected": sum(v["stale_law_uncorrected"] > 0 for v in vals),
        }
    # checker
    chk = None
    cf = run / "checker_seeded_faults.json"
    if cf.exists():
        res = load(cf)
        tp = sum(1 for r in res if not r["truth"] and r["supported"] is False)
        fn = sum(1 for r in res if not r["truth"] and r["supported"] is not False)
        fp = sum(1 for r in res if r["truth"] and r["supported"] is False)
        chk = {"wrong_pairs_caught": tp, "wrong_pairs_missed": fn, "right_pairs_wrongly_flagged": fp, "n_wrong": tp + fn, "n_right": len(res) - tp - fn}
    # cost/latency from the log for this run's tags
    log = ROOT / "logs" / "calls.jsonl"
    case_ids = set(cases)
    calls = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    meta = load(run / "meta.json") if (run / "meta.json").exists() else {}
    t0 = meta.get("started", "0000")
    t1 = meta.get("finished", "9999")
    mine = [x for x in calls if t0 <= x["ts"] <= t1 and (x["tag"].split("__r")[0] in case_ids) and "error" not in x]
    per_case = {}
    for x in mine:
        per_case.setdefault(x["tag"], []).append(x)
    costs = [sum(y["cost_inr"] for y in v) for v in per_case.values()]
    ntok_in = [sum(y["in_tokens"] for y in v) for v in per_case.values()]
    ntok_out = [sum(y["out_tokens"] for y in v) for v in per_case.values()]
    cost = {"pipeline_runs": len(per_case), "calls": len(mine),
            "avg_calls_per_case": round(len(mine) / max(1, len(per_case)), 2),
            "avg_cost_inr_per_case": round(sum(costs) / max(1, len(costs)), 3),
            "avg_in_tokens": round(sum(ntok_in) / max(1, len(ntok_in))), "avg_out_tokens": round(sum(ntok_out) / max(1, len(ntok_out))),
            "median_seconds_per_case": sorted(r["seconds"] for r in rows if r.get("seconds"))[len(rows) // 2] if rows else None}
    by_cat = {}
    for r in rows:
        b = by_cat.setdefault(r["cat"], [0, 0])
        b[0] += r["pass"]
        b[1] += 1
    summary = {"run": run.name, "by_category": {k: f"{v[0]} of {v[1]}" for k, v in sorted(by_cat.items())},
               "total": f"{sum(r['pass'] for r in rows)} of {len(rows)}",
               "first_try_violation_cases": sum(r.get("first_try_violations", 0) > 0 for r in rows),
               "cases_still_failing_after_retry": sum(r.get("final_violations", 0) > 0 for r in rows),
               "metamorphic": pair_res, "language_groups": group_res,
               "repeats_all3_pass": {k: f"{sum(v)} of {len(v)}" for k, v in rep.items()},
               "baseline_vs_pipeline": comp, "support_checker_seeded_faults": chk, "cost_latency": cost,
               "failures": [{"id": r["id"], "failed": [k for k, v in r["checks"].items() if not v], "error": r.get("error")} for r in rows if not r["pass"]]}
    (run / "grades.json").write_text(json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=1))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--grade", help="grade an existing run directory only")
    ap.add_argument("--cases", help="path to a cases module (default evals/cases.py); e.g. evals/heldout_cases.py")
    ap.add_argument("--no-repeats", action="store_true")
    ap.add_argument("--no-baselines", action="store_true")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    C = load_cases(a.cases)
    tag = ("_heldout" if a.cases and "heldout" in a.cases else "") + a.tag
    run = Path(a.grade) if a.grade else run_all(C, tag, repeats=not a.no_repeats, baselines=not a.no_baselines)
    s = grade(run, C)
    print(json.dumps(s, ensure_ascii=False, indent=1))
