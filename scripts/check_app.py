"""
check_app.py -- Use Playwright to upload a PDF to the Streamlit app,
wait for profiling to complete, and take a screenshot of the bbox results.

Usage: python scripts/check_app.py [pdf_path]
       python scripts/check_app.py icmr/cardiology/Cardiology_STWs/Atrial_Fibrillation.pdf
"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PDF = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "icmr/cardiology/Cardiology_STWs/Atrial_Fibrillation.pdf"
)
OUT_DIR = Path("data/validation/playwright")
OUT_DIR.mkdir(parents=True, exist_ok=True)

from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        print(f"Opening http://localhost:8501 ...")
        page.goto("http://localhost:8501", wait_until="networkidle")
        time.sleep(2)

        # Screenshot of landing page
        landing = OUT_DIR / "01_landing.png"
        page.screenshot(path=str(landing), full_page=True)
        print(f"Landing page screenshot: {landing}")

        # Upload the PDF
        print(f"Uploading: {PDF.name} ...")
        file_input = page.locator('input[type="file"]')
        file_input.set_input_files(str(PDF.resolve()))

        # Wait for profiling to complete — wait for the annotated image to appear
        print("Waiting for profiling to complete...")
        try:
            # Wait for the strategy badge (appears after profiling finishes)
            page.wait_for_selector("text=hybrid, text=text_mode, text=poster_mode",
                                   timeout=120000)
            print("Profiling done!")
        except Exception:
            # Fallback: wait for any image element to appear
            try:
                page.wait_for_selector("img", timeout=120000)
                print("Image appeared!")
            except Exception:
                print("Timeout — taking screenshot anyway")

        # Extra wait for full render
        time.sleep(4)

        # Full page screenshot showing bboxes
        full = OUT_DIR / f"02_bboxes_{PDF.stem}.png"
        page.screenshot(path=str(full), full_page=True)
        print(f"Bbox screenshot: {full}")

        # Screenshot of just the image area
        try:
            img_elem = page.locator("img").first
            img_elem.screenshot(path=str(OUT_DIR / f"03_annotated_{PDF.stem}.png"))
            print(f"Annotated image: {OUT_DIR}/03_annotated_{PDF.stem}.png")
        except Exception as e:
            print(f"Could not screenshot image element: {e}")

        browser.close()
        print(f"\nAll screenshots saved to: {OUT_DIR}/")

if __name__ == "__main__":
    run()
