"""Measurable evaluation on SYNTHETIC cases. Run: python -m eval.run_eval
Metrics: red-flag recall/precision, hallucination catch-rate (injected fake
findings must be removed by grounding), mean pipeline latency.
Replace cases.json with clinician-labelled, consented/de-identified data
before making any real-world claim."""
import json, statistics, pathlib
from app.pipeline import run_case
from app import grounding

cases_path = pathlib.Path(__file__).with_name("cases.json")

with open(cases_path, "r", encoding="utf-8") as f:
    cases = json.load(f)
tp = fp = fn = 0; lat = []; missed = []
for c in cases:
    r = run_case(c["text"] * 1)
    got = {f["id"] for f in r["red_flags"]}; exp = set(c["red"])
    tp += len(got & exp); fp += len(got - exp); fn += len(exp - got)
    if exp - got: missed.append((c["id"], exp - got))
    lat.append(r["meta"]["latency_ms"])
rec = tp / (tp + fn) if tp + fn else 1.0; prec = tp / (tp + fp) if tp + fp else 1.0

# hallucination test: fabricated findings whose quotes are NOT in the source
fakes = ["Patient was diagnosed with Alzheimer's disease last year.", "He has stopped taking all medication.",
         "She recognises nobody in the family anymore.", "A CT scan showed a tumour."]
caught = sum(grounding.verify_quote(q, c["text"])[0] == "missing" for c in cases for q in fakes)
total = len(cases) * len(fakes)
real = sum(grounding.verify_quote(c["text"].split(".")[0] + ".", c["text"])[0] != "missing" for c in cases)

print(f"cases: {len(cases)}")
print(f"red-flag recall   : {rec:.2f}   precision: {prec:.2f}   missed: {missed}")
print(f"hallucinations caught by grounding: {caught}/{total}")
print(f"true quotes kept  : {real}/{len(cases)}")
print(f"mean pipeline latency: {statistics.mean(lat):.1f} ms (rules+mock; live LLM adds model time)")
