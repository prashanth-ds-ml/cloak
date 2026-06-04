"""Compare 3 batch runs"""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

data = json.loads(Path("data/extraction/batch_summary.json").read_text(encoding="utf-8"))

# Run 2 baseline (3 fixes, no post-processor)
run2 = {
    "Atrial_Fibrillation":      (8.5, 0.66),
    "Stroke":                   (8.0, 0.72),
    "Dengue_Fever":             (9.0, 0.38),
    "Acute_Kidney_Injury":      (9.0, 0.71),
    "Epistaxis":                (9.0, 0.38),
    "STEMI":                    (8.0, 0.32),
    "Headache":                 (8.0, 0.31),
    "Diarrhea":                 (8.0, 0.77),
    "Heavy_Menstrual_Bleeding": (9.0, 0.31),
    "Acute_Rhinosinusitis":     (9.0, 0.43),
}

print("Run 2 vs Run 3 (with post-processor)")
print(f"  {'Document':<32} {'R2':>5} {'R3':>5} {'Chg':>5}  {'Recall':>8}")
print("-" * 65)
for d in data:
    name = d["name"]
    j3 = d.get("judgment", {}).get("overall", "—")
    r3 = d.get("landing_ai_comparison", {}).get("word_recall", "—")
    j2, r2 = run2.get(name, ("—", "—"))
    try:
        chg = float(j3) - float(j2)
        chg_s = f"+{chg:.1f}" if chg > 0 else f"{chg:.1f}" if chg < 0 else "same"
    except Exception:
        chg_s = "—"
    print(f"  {name:<32} {str(j2):>5} {str(j3):>5} {chg_s:>5}  {str(r3):>8}")

judges = [float(d.get("judgment", {}).get("overall", 0))
          for d in data if d.get("judgment", {}).get("overall")]
print(f"\nAvg: {sum(judges)/len(judges):.2f}/10")
cnt9  = sum(1 for j in judges if j >= 9.0)
cnt85 = sum(1 for j in judges if j == 8.5)
cnt8  = sum(1 for j in judges if j == 8.0)
print(f"9+/10: {cnt9}  8.5: {cnt85}  8: {cnt8}")

print("\nJudge feedback on remaining issues:")
for d in data:
    name = d["name"]
    j = d.get("judgment", {})
    missing = j.get("missing", [])
    issues  = j.get("issues", [])
    if missing and missing != ["none"] and missing != []:
        print(f"  {name}: missing={missing[:2]}")
    if issues and issues != ["none"] and issues != []:
        print(f"  {name}: issues={issues[:2]}")
