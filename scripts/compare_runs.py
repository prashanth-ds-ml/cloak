"""compare_runs.py -- compare profiling results against ground truth"""
import json, sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

data = json.loads(Path("data/batch_logs/profile2_summary.json").read_text(encoding="utf-8"))
by_name = {d["name"]: d for d in data}

# Known ground truth from visual inspection of the documents
GROUND_TRUTH_COLS = {
    "cardiology_af": 3,
    "cardiology_stemi": 2,
    "cardiology_bradyarrhythmia": 2,
    "neurology_stroke": 2,
    "neurology_epilepsy": 3,
    "paediatrics_dengue": 1,
    "paediatrics_diarrhea": 5,
    "psychiatry_depression": 2,
    "ortho_supracondylar": 5,
    "tb_paed_lymphnode": 5,
    "neonatology_sepsis": 2,
    "cardiology_nstemi": 2,  # main content is 2-col
    "cardiology_heart_failure": 2,
    "neurology_headache": 2,
    "neurology_dementia": 2,
}

print("Column detection vs ground truth:")
print(f"{'Document':<42} {'Expected':>8} {'Got':>5} {'Status':>6}")
print("-" * 65)
correct = 0
wrong = 0
for name, expected in sorted(GROUND_TRUTH_COLS.items()):
    d = by_name.get(name, {})
    got = d.get("columns", "?")
    ok = got == expected
    if ok:
        correct += 1
        status = "OK"
    else:
        wrong += 1
        status = "WRONG"
    marker = " <-- needs fix" if not ok else ""
    print(f"{name:<42} {expected:>8} {got:>5}  {status}{marker}")

print(f"\nCorrect: {correct}/{correct+wrong}")

# Hallucination detections
print("\nHallucinations detected (Fix 1):")
halluc = [d["name"] for d in data if d.get("glm_hallucination", False)]
if halluc:
    for h in halluc:
        print(f"  {h}")
else:
    print("  (none detected)")

# Known hallucinations that should be caught
KNOWN_HALLUC = ["cardiology_nstemi", "neurosurg_spinal", "obgyn_antenatal"]
print("\nKnown hallucination docs - were they caught?")
for h in KNOWN_HALLUC:
    caught = by_name.get(h, {}).get("glm_hallucination", False)
    sections = len(by_name.get(h, {}).get("glm_sections", []))
    print(f"  {h}: caught={caught}  sections_kept={sections}")

# Multi-page docs (Fix 2)
print("\nMulti-page documents detected (Fix 2):")
multi = [(d["name"], d["page_count"]) for d in data if d.get("page_count", 1) > 1]
for name, pc in sorted(multi, key=lambda x: -x[1]):
    strat = by_name[name]["strategy"]
    print(f"  {name:<42} {pc} pages  {strat}")

# Column distribution comparison
from collections import Counter
col_dist = Counter(d["columns"] for d in data)
print("\nColumn distribution:")
for c in sorted(col_dist):
    print(f"  {c} cols: {col_dist[c]} docs")
