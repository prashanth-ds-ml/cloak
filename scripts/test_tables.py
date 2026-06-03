"""
test_tables.py -- Compare pdfplumber vs camelot on the CHA2DS2-VASc table.
Uses exact bbox from Landing.ai JSON. Compares against Landing.ai output.

Usage: python scripts/test_tables.py
"""
import sys, io, json, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pdfplumber

PDF = Path("data/samples/icmr_stw_full/cardiology/cardiology_af.pdf")
LA_JSON = Path("C:/Users/prash/Downloads/1768823343_cardiology_1-1.parse.json")

# Landing.ai reference
LA_TABLE = """| CHA2DS2-VASc | SCORE | HAS-BLED | SCORE |
| --- | --- | --- | --- |
| Congestive heart failure/LV dysfunction | 1 | Hypertension i.e. uncontrolled BP | 1 |
| Hypertension | 1 | Abnormal renal/liver function | 1 or 2 |
| Aged >= 75 years | 2 | Stroke | 1 |
| Diabetes mellitus | 1 | Bleeding tendency or predisposition | 1 |
| Stroke/TIA/TE | 2 | Labile INR | 1 |
| Vascular disease [prior MI, PAD or aortic plaque] | 1 | Age (e.g. >65) | 1 |
| Aged 65-74 years | 1 | Drugs (e.g. concomitant aspirin or NSAIDs or alcohol) | 1 |
| Maximum Score | 9 | | 9 |"""


def words(text):
    return set(re.findall(r'\b[a-zA-Z0-9]{2,}\b', text.lower()))


def score(output, reference):
    o, r = words(output), words(reference)
    recall = round(len(o & r) / len(r) if r else 0, 2)
    precision = round(len(o & r) / len(o) if o else 0, 2)
    return recall, precision


def table_to_markdown(rows):
    if not rows:
        return ""
    lines = []
    for i, row in enumerate(rows):
        cells = [str(c or "").replace("\n", " ").strip() for c in row]
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
    return "\n".join(lines)


def main():
    # Load Landing.ai bboxes
    la = json.loads(LA_JSON.read_text(encoding="utf-8"))
    grounding = la["grounding"]

    # CHA2DS2-VASc table bbox from Landing.ai
    table_id = "6c338217-a67b-40a6-b658-45b0782fbaee"
    g = grounding[table_id]["box"]
    print(f"CHA2DS2-VASc table bbox (normalized): {g}")

    with pdfplumber.open(PDF) as pdf:
        page = pdf.pages[0]
        W, H = page.width, page.height
        print(f"Page: {W}x{H} pts\n")

        # Convert normalized bbox to PDF points
        x0 = g["left"] * W
        top = g["top"] * H
        x1 = g["right"] * W
        bottom = g["bottom"] * H
        print(f"Bbox in pts: x0={x0:.0f} top={top:.0f} x1={x1:.0f} bottom={bottom:.0f}")
        print()

        # ── TEST 1: pdfplumber full page table detection ──────────────────────
        print("=" * 60)
        print("TEST 1 — pdfplumber: full page find_tables()")
        print("=" * 60)
        all_tables = page.find_tables()
        print(f"Tables found on page: {len(all_tables)}")
        for i, t in enumerate(all_tables):
            tx0, ty0, tx1, ty1 = t.bbox
            rows = t.extract()
            print(f"\nTable {i+1}: x={tx0/W*100:.0f}-{tx1/W*100:.0f}%  y={ty0/H*100:.0f}-{ty1/H*100:.0f}%  {len(rows)}r x {len(rows[0]) if rows else 0}c")
            if ty0/H > 0.28 and ty1/H < 0.45:  # This is the CHA2DS2-VASc range
                md = table_to_markdown(rows)
                print("  Markdown output:")
                for line in md.splitlines():
                    print(f"    {line}")
                r, p = score(md, LA_TABLE)
                print(f"  vs Landing.ai: recall={r}  precision={p}")

        # ── TEST 2: pdfplumber within_bbox extraction ─────────────────────────
        print("\n" + "=" * 60)
        print("TEST 2 — pdfplumber: within_bbox() on Landing.ai table region")
        print("=" * 60)
        cropped = page.within_bbox((x0, top, x1, bottom))
        tables_in_bbox = cropped.find_tables()
        print(f"Tables found in bbox: {len(tables_in_bbox)}")
        if tables_in_bbox:
            rows = tables_in_bbox[0].extract()
            md = table_to_markdown(rows)
            print("Markdown output:")
            for line in md.splitlines():
                print(f"  {line}")
            r, p = score(md, LA_TABLE)
            print(f"\nvs Landing.ai: recall={r}  precision={p}")
        else:
            raw_text = cropped.extract_text() or ""
            print("No tables found, raw text:")
            print(raw_text[:500])

        # ── TEST 3: camelot lattice ──────────────────────────────────────────
        print("\n" + "=" * 60)
        print("TEST 3 — camelot: lattice mode (detects grid lines visually)")
        print("=" * 60)
        try:
            import camelot
            # camelot uses bottom-left origin: y from bottom
            # convert: camelot_y = page_height - pdfplumber_y
            c_x0 = x0
            c_y0 = H - bottom   # top of bbox in camelot coords
            c_x1 = x1
            c_y1 = H - top      # bottom of bbox in camelot coords
            table_area = f"{c_x0:.0f},{c_y0:.0f},{c_x1:.0f},{c_y1:.0f}"
            print(f"Camelot table_areas: {table_area}")

            tables = camelot.read_pdf(
                str(PDF),
                pages="1",
                flavor="lattice",
                table_areas=[table_area],
            )
            print(f"Tables found: {len(tables)}")
            if tables:
                t = tables[0]
                print(f"Accuracy: {t.accuracy:.1f}%  Whitespace: {t.whitespace:.1f}%")
                df = t.df
                # Convert dataframe to markdown
                rows = [df.columns.tolist()] + df.values.tolist()
                # Actually just use the df directly
                md_rows = []
                for i, row in df.iterrows():
                    cells = [str(v).replace("\n", " ").strip() for v in row]
                    md_rows.append("| " + " | ".join(cells) + " |")
                    if i == 0:
                        md_rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
                md = "\n".join(md_rows)
                print("\nMarkdown output:")
                for line in md.splitlines():
                    print(f"  {line}")
                r, p = score(md, LA_TABLE)
                print(f"\nvs Landing.ai: recall={r}  precision={p}")
        except Exception as exc:
            print(f"Camelot error: {exc}")

        # ── TEST 4: camelot stream mode ──────────────────────────────────────
        print("\n" + "=" * 60)
        print("TEST 4 — camelot: stream mode (whitespace-based detection)")
        print("=" * 60)
        try:
            import camelot
            tables = camelot.read_pdf(
                str(PDF),
                pages="1",
                flavor="stream",
                table_areas=[table_area],
            )
            print(f"Tables found: {len(tables)}")
            if tables:
                t = tables[0]
                print(f"Accuracy: {t.accuracy:.1f}%  Whitespace: {t.whitespace:.1f}%")
                df = t.df
                md_rows = []
                for i, row in df.iterrows():
                    cells = [str(v).replace("\n", " ").strip() for v in row]
                    md_rows.append("| " + " | ".join(cells) + " |")
                    if i == 0:
                        md_rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
                md = "\n".join(md_rows)
                print("\nMarkdown output:")
                for line in md.splitlines():
                    print(f"  {line}")
                r, p = score(md, LA_TABLE)
                print(f"\nvs Landing.ai: recall={r}  precision={p}")
        except Exception as exc:
            print(f"Camelot stream error: {exc}")

    # Reference
    print("\n" + "=" * 60)
    print("LANDING.AI REFERENCE TABLE:")
    print("=" * 60)
    for line in LA_TABLE.splitlines():
        print(f"  {line}")


if __name__ == "__main__":
    main()
