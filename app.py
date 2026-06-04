"""
app.py -- Cloak Document Profiler — visual inspection tool.

Upload a PDF, profiler runs in background, bboxes appear on the page image.
Inspect layout visually: text (blue), tables (green), content gaps (red), columns (yellow).

Run: streamlit run app.py
Opens at http://localhost:8501
"""
import sys, io, time, tempfile
from pathlib import Path

import streamlit as st
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))

# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Cloak Profiler",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.metric-row { display: flex; gap: 12px; flex-wrap: wrap; }
.metric-box { background: #1e1e1e; border-radius: 8px; padding: 10px 16px; min-width: 120px; }
.metric-label { font-size: 12px; color: #888; }
.metric-value { font-size: 22px; font-weight: bold; color: #fff; }
.badge-text    { background: #1a7a3a; color: #fff; padding: 2px 10px; border-radius: 12px; }
.badge-hybrid  { background: #7a5a1a; color: #fff; padding: 2px 10px; border-radius: 12px; }
.badge-poster  { background: #7a1a1a; color: #fff; padding: 2px 10px; border-radius: 12px; }
</style>
""", unsafe_allow_html=True)


# ── annotate page image ────────────────────────────────────────────────────────

def _get_table_bboxes(pdf_path_str: str):
    """Get table bboxes from pdfplumber as normalized (x0,y0,x1,y1,rows,cols) tuples."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path_str) as pdf:
            page = pdf.pages[0]
            W, H = page.width, page.height
            result = []
            for t in page.find_tables():
                x0, y0, x1, y1 = t.bbox
                rows = t.extract()
                nr = len(rows) if rows else 0
                nc = len(rows[0]) if rows and rows[0] else 0
                result.append((x0/W, y0/H, x1/W, y1/H, nr, nc))
            return result
    except Exception:
        return []


def build_annotated_image(page_img: Image.Image, prof) -> Image.Image:
    """Draw bboxes and labels on the page image for visual inspection."""
    img = page_img.copy().convert("RGB")

    # Scale to max 900px tall for display
    W, H = img.size
    if H > 900:
        scale = 900 / H
        img = img.resize((int(W * scale), 900), Image.LANCZOS)

    W, H = img.size
    draw = ImageDraw.Draw(img, "RGBA")

    # ── Column boundaries — yellow dashed vertical lines ──────────────────────
    for b_pct in prof.columns.boundaries_pct:
        x = int(b_pct / 100 * W)
        for y in range(0, H, 18):
            draw.line([(x, y), (x, min(y + 10, H))], fill=(255, 220, 0, 200), width=3)

    # ── Docling section headers — blue dots ───────────────────────────────────
    for h in prof.docling_sections:
        x = int(h["x_pct"] / 100 * W)
        y = int(h["y_pct"] / 100 * H)
        draw.ellipse([x-5, y-5, x+5, y+5], fill=(50, 120, 255, 200))

    # ── pdfplumber tables — green boxes (fetched fresh, pdf_tables is int count)
    for (x0n, y0n, x1n, y1n, nr, nc) in _get_table_bboxes(prof.path):
        x0, y0 = int(x0n * W), int(y0n * H)
        x1, y1 = int(x1n * W), int(y1n * H)
        draw.rectangle([x0, y0, x1, y1], outline=(50, 200, 80, 220), width=3)
        draw.rectangle([x0, y0, x0 + 80, y0 + 18], fill=(50, 200, 80, 180))
        draw.text((x0 + 3, y0 + 2), f"table {nr}r×{nc}c", fill="white")

    # ── Picture sections (content gaps) — red boxes ───────────────────────────
    for i, p in enumerate(prof.picture_sections):
        if p.area_pct < 3:
            continue
        x0 = int(p.x0_pct / 100 * W)
        y0 = int(p.y0_pct / 100 * H)
        x1 = int(p.x1_pct / 100 * W)
        y1 = int(p.y1_pct / 100 * H)
        type_label = p.vlm_type if p.vlm_type else "?"
        draw.rectangle([x0, y0, x1, y1], outline=(220, 50, 50, 220), width=4)
        draw.rectangle([x0, y0, x0 + 120, y0 + 18], fill=(220, 50, 50, 180))
        draw.text((x0 + 3, y0 + 2), f"gap {i+1}: {type_label} ({p.area_pct:.0f}%)", fill="white")

    # ── Header bar ─────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, W, 32], fill=(0, 0, 0, 200))
    strategy = prof.strategy.split("_multi")[0]
    info = (f"  {prof.name}  |  strategy={strategy}  "
            f"|  cov={prof.docling_coverage_pct}%  "
            f"|  cols={prof.columns.count}  "
            f"|  glm={'ok' if prof.glm_chars else 'fail'}")
    draw.text((4, 8), info, fill=(255, 255, 255, 220))

    # ── Legend ─────────────────────────────────────────────────────────────────
    legend_y = H - 28
    draw.rectangle([0, legend_y, W, H], fill=(0, 0, 0, 200))
    legend = "  • Yellow=columns   • Blue dots=headings   • Green=tables   • Red=content gaps"
    draw.text((4, legend_y + 8), legend, fill=(200, 200, 200, 220))

    return img


# ── main app ───────────────────────────────────────────────────────────────────

def main():
    st.title("🔍 Cloak Document Profiler")
    st.caption("Upload a PDF — profiler runs, bboxes show what was detected")

    # ── Upload ─────────────────────────────────────────────────────────────────
    uploaded = st.file_uploader(
        "Drop a PDF here or click to browse",
        type=["pdf"],
        label_visibility="collapsed",
    )

    if not uploaded:
        st.info("👆 Upload a PDF to start profiling")
        st.markdown("""
        **What you'll see:**
        - 🟡 Yellow dashed lines = detected column boundaries
        - 🔵 Blue dots = section heading positions
        - 🟢 Green boxes = tables (pdfplumber detected)
        - 🔴 Red boxes = content gaps (picture sections — need VLM/camelot)
        """)
        return

    # ── Save PDF to temp ───────────────────────────────────────────────────────
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(uploaded.read())
        pdf_path = Path(f.name)

    try:
        # ── Run profiler ───────────────────────────────────────────────────────
        from scripts.profiler_v2 import pass1_profile, pass2_vlm

        progress_container = st.empty()
        with progress_container.container():
            with st.status(f"Profiling {uploaded.name}...", expanded=True) as status:
                t0 = time.monotonic()
                st.write("🔍 Running docling layout analysis...")
                prof = pass1_profile(pdf_path)
                n_elems = sum(prof.docling_label_counts.values()) if prof.docling_label_counts else 0
                st.write(f"✓ docling: {time.monotonic()-t0:.1f}s  |  "
                         f"coverage={prof.docling_coverage_pct}%  |  "
                         f"elements={n_elems}")

                if prof.glm_chars > 0:
                    st.write(f"✓ GLM-OCR: {prof.glm_chars} chars in {prof.glm_time_s}s")
                else:
                    st.write("⚠ GLM-OCR: failed (GGML error — content from pdfplumber)")

                if prof.tags:
                    st.write(f"🤖 Running VLM Pass 2 for: {prof.tags}...")
                    prof = pass2_vlm(prof)
                    st.write(f"✓ VLM Pass 2 done")

                total = round(time.monotonic() - t0, 1)
                status.update(label=f"✓ Profile complete ({total}s)", state="complete")

        progress_container.empty()

        # ── Load page image ────────────────────────────────────────────────────
        from cloak.extraction.pdf_tools import load_pages
        pages = load_pages(pdf_path)
        page_img = pages[0].image

        # ── Build annotated image ──────────────────────────────────────────────
        annotated = build_annotated_image(page_img, prof)

        # ── Layout: image (left) + profile (right) ─────────────────────────────
        col_img, col_info = st.columns([3, 1])

        with col_img:
            buf = io.BytesIO()
            annotated.save(buf, format="PNG")
            st.image(buf.getvalue(), use_container_width=True,
                     caption=f"{uploaded.name} — page 1/{prof.page_count}")

        with col_info:
            # Strategy badge
            strategy = prof.strategy.split("_multi")[0]
            badge_class = {"text_mode": "text", "hybrid": "hybrid",
                           "poster_mode": "poster"}.get(strategy, "text")
            st.markdown(f"<span class='badge-{badge_class}'>{strategy}</span>",
                        unsafe_allow_html=True)

            st.markdown("---")
            st.metric("Docling coverage", f"{prof.docling_coverage_pct}%",
                      delta="high" if prof.docling_coverage_pct > 60 else "low")
            st.metric("Columns detected", prof.columns.count)
            st.metric("Column boundaries",
                      str(prof.columns.boundaries_pct) if prof.columns.boundaries_pct else "none")
            st.metric("GLM-OCR chars", prof.glm_chars)
            st.metric("PDF tables", prof.pdf_tables)
            st.metric("Content gaps", sum(1 for p in prof.picture_sections if p.area_pct > 5))

            if prof.page_count > 1:
                st.warning(f"Multi-page: {prof.page_count} pages (profiling page 1 only)")

            st.markdown("---")
            st.write(f"**Tags:** {', '.join(prof.tags) if prof.tags else 'none'}")

            # GLM-OCR sections
            if prof.glm_sections:
                st.markdown(f"**GLM-OCR sections ({len(prof.glm_sections)}):**")
                for s in prof.glm_sections[:8]:
                    st.write(f"• {s[:45]}")
                if len(prof.glm_sections) > 8:
                    st.caption(f"...+{len(prof.glm_sections)-8} more")
            elif prof.glm_hallucination:
                st.error("GLM-OCR hallucination detected — sections discarded")

        # ── Element details ─────────────────────────────────────────────────────
        with st.expander(f"📋 Docling section headers ({len(prof.docling_sections)} found)", expanded=False):
            if prof.docling_sections:
                import pandas as pd
                df = pd.DataFrame([
                    {"X%": h["x_pct"], "Y%": h["y_pct"], "Text": h["text"][:60]}
                    for h in sorted(prof.docling_sections, key=lambda x: x["y_pct"])
                ])
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.write("No section headers detected by docling.")

        with st.expander(f"🟥 Content gaps / Picture sections ({len(prof.picture_sections)} total)", expanded=True):
            large_pics = [p for p in prof.picture_sections if p.area_pct > 3]
            if large_pics:
                for p in sorted(large_pics, key=lambda x: x.y0_pct):
                    col_a, col_b, col_c = st.columns([1, 1, 3])
                    col_a.metric("Y position", f"{p.y0_pct:.0f}–{p.y1_pct:.0f}%")
                    col_b.metric("Area / Type",
                                 f"{p.area_pct:.0f}% / {p.vlm_type or '?'}")
                    col_c.write(f"**pdfplumber text ({p.pdf_words} words):**")
                    if p.pdf_text:
                        col_c.code(p.pdf_text[:200], language=None)
                    else:
                        col_c.caption("(no text extracted)")
            else:
                st.success("No large content gaps — docling captured everything.")

        with st.expander(f"🟩 Tables ({prof.pdf_tables} found by pdfplumber)", expanded=False):
            table_bboxes = _get_table_bboxes(prof.path)
            if table_bboxes:
                import pandas as pd
                df = pd.DataFrame([
                    {"#": i+1,
                     "Y%": f"{y0*100:.0f}–{y1*100:.0f}",
                     "X%": f"{x0*100:.0f}–{x1*100:.0f}",
                     "Size": f"{nr}r × {nc}c"}
                    for i, (x0, y0, x1, y1, nr, nc) in enumerate(table_bboxes)
                ])
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.write("No tables detected.")

        with st.expander("📊 Raw profile (JSON)", expanded=False):
            from dataclasses import asdict
            import json
            d = asdict(prof)
            d.pop("glm_text", None)   # too large
            st.json(d)

    finally:
        pdf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
