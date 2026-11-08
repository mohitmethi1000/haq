"""CLI: python run_case.py "meri company ne 2 mahine se salary nahi di..." [--today 2026-11-08]"""
import argparse, json
from datetime import date
from haq.llm import LLM
from haq.pipeline import run_case

p = argparse.ArgumentParser()
p.add_argument("story")
p.add_argument("--today", default=date.today().isoformat())
p.add_argument("--no-support", action="store_true")
a = p.parse_args()
out = run_case(LLM(tag="cli"), a.story, date.fromisoformat(a.today), support_check=not a.no_support)
print(json.dumps(out, ensure_ascii=False, indent=1))
