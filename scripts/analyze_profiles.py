"""analyze_profiles.py -- analyze all profiling results"""
import json, sys, io, statistics
from pathlib import Path
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

data = json.loads(Path("data/batch_logs/profile2_summary.json").read_text(encoding="utf-8"))

print(f"Total documents profiled: {len(data)}")

# Strategy
strategies = Counter(d["strategy"] for d in data)
print(f"\nStrategy distribution:")
for s, c in strategies.most_common():
    print(f"  {s:<12} {c:>3} docs  ({c/len(data)*100:.0f}%)")

# Columns
cols = Counter(d["columns"] for d in data)
print(f"\nColumn counts:")
for c, n in sorted(cols.items()):
    print(f"  {c} cols: {n} docs")

# 4+ column docs
print(f"\n4+ column layouts:")
for d in sorted([d for d in data if d["columns"] >= 4], key=lambda x: -x["columns"]):
    print(f"  {d['name']:<42} {d['columns']} cols  boundaries={d['col_boundaries']}")

# GLM-OCR
glm_ok = sum(1 for d in data if d["glm_chars"] > 0)
print(f"\nGLM-OCR: {glm_ok}/{len(data)} success ({glm_ok/len(data)*100:.0f}%)")

# Coverage stats
covs = [d["coverage"] for d in data]
print(f"\nDocling coverage across 158 docs:")
print(f"  Min={min(covs):.1f}%  Max={max(covs):.1f}%  Mean={statistics.mean(covs):.1f}%  Median={statistics.median(covs):.1f}%")

buckets = {"0-10%":0,"10-30%":0,"30-60%":0,"60-80%":0,"80-100%":0}
for c in covs:
    if c < 10: buckets["0-10%"] += 1
    elif c < 30: buckets["10-30%"] += 1
    elif c < 60: buckets["30-60%"] += 1
    elif c < 80: buckets["60-80%"] += 1
    else: buckets["80-100%"] += 1
for b, n in buckets.items():
    print(f"  {b}: {n} docs")

# Picture sections with lots of content
print(f"\nDocs with >3000 chars inside picture sections (content gaps):")
big = sorted([(d["name"], d["picture_text_chars"], d["picture_sections"])
              for d in data if d["picture_text_chars"] > 3000], key=lambda x: -x[1])
for name, chars, n in big[:20]:
    print(f"  {name:<42} {chars:>5} chars  ({n} pics)")

# Poster mode low coverage
print(f"\nPoster mode documents (lowest coverage first):")
poster = sorted([d for d in data if d["strategy"] == "poster_mode"], key=lambda x: x["coverage"])
for d in poster:
    print(f"  {d['name']:<42} cov={d['coverage']:5.1f}%  glm={d['glm_chars']:>4}  pics={d['picture_sections']}")

# Documents with most GLM-OCR sections found
print(f"\nDocs with most GLM-OCR sections found:")
section_counts = sorted(data, key=lambda x: -len(x.get("glm_sections", [])))
for d in section_counts[:15]:
    n = len(d.get("glm_sections", []))
    if n == 0: break
    print(f"  {d['name']:<42} {n} sections")
