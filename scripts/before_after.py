"""before_after.py -- before/after comparison of profiler fixes"""
import json, sys, io
from pathlib import Path
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
data = json.loads(Path("data/batch_logs/profile2_summary.json").read_text(encoding="utf-8"))
by_name = {d["name"]: d for d in data}

print("=" * 60)
print("PROFILER FIXES — BEFORE vs AFTER")
print("=" * 60)

print("\nFix 1 — Hallucination detection:")
print("  Before: 1/3 caught (nstemi only)")
halluc = [d["name"] for d in data if d.get("glm_hallucination")]
known = ["cardiology_nstemi", "neurosurg_spinal", "obgyn_antenatal"]
print(f"  After:  {len(halluc)}/3 known hallucinations caught")
for h in known:
    caught = by_name.get(h, {}).get("glm_hallucination", False)
    kept = len(by_name.get(h, {}).get("glm_sections", []))
    status = "CAUGHT" if caught else f"MISSED ({kept} fake sections kept)"
    print(f"    {h}: {status}")

print("\nFix 2 — Multi-page detection:")
multi = [(d["name"], d.get("page_count", 1)) for d in data if d.get("page_count", 1) > 1]
print(f"  Before: 0 (page_count not in summary JSON)")
print(f"  After:  {len(multi)} multi-page docs identified")
for n, p in sorted(multi):
    strat = by_name[n]["strategy"]
    print(f"    {n}: {p}pp  ({strat})")

print("\nFix 4 — Column detection (key cases):")
print(f"  {'Document':<38} {'Before':>7} {'Expected':>9} {'After':>6} {'Status':>8}")
print("-" * 75)
checks = [
    ("cardiology_af", 2, 3),
    ("cardiology_stemi", 2, 2),
    ("cardiology_bradyarrhythmia", 2, 2),
    ("neurology_stroke", 2, 2),
    ("neurology_epilepsy", 3, 3),
    ("paediatrics_dengue", 1, 1),
    ("ortho_supracondylar", 5, 5),
    ("paediatrics_diarrhea", 5, 5),
    ("psychiatry_depression", 2, 2),
]
for name, before, expected in checks:
    after = by_name.get(name, {}).get("columns", "?")
    if after == expected and before != expected:
        status = "IMPROVED"
    elif after == expected:
        status = "OK"
    else:
        status = "WRONG"
    print(f"  {name:<38} {before:>7} {expected:>9} {str(after):>6}  {status}")

print("\nStrategy distribution (final 158 docs):")
strats = Counter(d["strategy"].split("_multi")[0] for d in data)
for s, c in strats.most_common():
    print(f"  {s}: {c} ({c/len(data)*100:.0f}%)")

print("\nColumn distribution (final):")
cols = Counter(d["columns"] for d in data)
for c in sorted(cols):
    print(f"  {c} cols: {cols[c]} docs")

print("\nGLM-OCR success: ", sum(1 for d in data if d.get("glm_chars", 0) > 0), "/ 158")
