from pathlib import Path
import json as _json

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR     = PROJECT_ROOT / "data"
RAW_DIR      = DATA_DIR / "raw"
MD_DIR       = DATA_DIR / "markdown"
IMG_DIR      = DATA_DIR / "images"

# ── Ollama models — defaults (overridden by .cloak_local.json if present) ──
ORCHESTRATOR_MODEL  = "qwen3:14b"         # 9.0 GB dense — FORMAT, PATCH, deep review (D49)
VISION_PRIMARY      = "qwen3-vl:8b"       # 6.1 GB — full GPU, figures + image pages + L4 judge (D49)
VISION_FALLBACK     = "qwen3-vl:4b"       # 3.3 GB — full GPU, fallback if 8b fails to load (D49)
GLM_OCR_MODEL       = "glm-ocr"           # 2.2 GB — #1 OmniDocBench, document-specialised OCR (D45)

# ── Local hardware override (written by `cloak setup`) ─────────────────────
# .cloak_local.json is machine-specific and git-ignored.
# Overrides any of the model constants above for this machine.
_LOCAL_CFG = PROJECT_ROOT / ".cloak_local.json"
if _LOCAL_CFG.exists():
    try:
        _overrides = _json.loads(_LOCAL_CFG.read_text(encoding="utf-8"))
        ORCHESTRATOR_MODEL = _overrides.get("ORCHESTRATOR_MODEL", ORCHESTRATOR_MODEL)
        VISION_PRIMARY     = _overrides.get("VISION_PRIMARY",     VISION_PRIMARY)
        VISION_FALLBACK    = _overrides.get("VISION_FALLBACK",    VISION_FALLBACK)
        # deep review override applied later after DEEP_REVIEW_MODEL is defined
        _DEEP_REVIEW_OVERRIDE = _overrides.get("DEEP_REVIEW_MODEL")
    except Exception:
        _DEEP_REVIEW_OVERRIDE = None
else:
    _DEEP_REVIEW_OVERRIDE = None

OLLAMA_BASE_URL     = "http://localhost:11434"

# ── Agentic parser knobs ───────────────────────────────────────────────────
MAX_ROUNDS          = 4        # max extract→judge→patch iterations
QUALITY_THRESHOLD   = 8.0      # stop early if score ≥ this
CONTEXT_TOKEN_LIMIT = 8_000    # compress history above this
CONTENT_LOSS_LIMIT  = 0.35     # revert patch if >35% chars removed

# ── Vision / rendering ─────────────────────────────────────────────────────
PAGE_DPI            = 150      # PNG render resolution
VISION_TIMEOUT      = 1800     # local — no network limit; give model full time to generate on CPU+GPU split
STALL_SECONDS       = 150      # no new tokens for this long → stall suspected; CPU+GPU split can pause 90-120s between tokens
AGENT_TIMEOUT       = 600      # local — think=True on patch needs time; no network limit
FORMAT_TIMEOUT      = 900      # gemma4:26b MoE FORMAT pass; was 1800s
MAX_AGENT_ITERS     = 10       # ReAct loop cap

# ── Image filtering ────────────────────────────────────────────────────────
MIN_IMAGE_BYTES     = 5_000    # ignore images smaller than this

MODEL_NUM_CTX       = 16384    # gemma4:26b — covers full patch context
FORMAT_NUM_CTX      = 32768    # gemma4:26b FORMAT — covers full-doc markdown
VISION_NUM_CTX      = 8192     # gemma4:26b vision — more room for image tokens vs old 4096
MAX_IMAGE_PX        = 1024     # long-edge cap for figures and region_describe
EXAM_MAX_IMAGE_PX   = 1536     # exam/slide pages — higher resolution for dense math/content
JUDGE_MAX_IMAGE_PX  = 512      # judge images — smaller for fast scoring (~70 visual tokens)
MODEL_KEEP_ALIVE    = -1       # keep_alive=-1 — model stays loaded until explicit phase-boundary unload

# ── System / hardware gate ──────────────────────────────────────────────────
MIN_FREE_RAM_GB         = 9.0   # minimum free RAM to enable vision model (D18)

# ── Page profiler thresholds ────────────────────────────────────────────────
SCANNED_TEXT_THRESHOLD  = 100   # chars below which a page is considered scanned (D21)
IMAGE_AREA_THRESHOLD    = 0.4   # image area ratio above which a page is image_heavy (D21)

# ── Quality gates ───────────────────────────────────────────────────────────
LOW_CONFIDENCE_THRESHOLD = 5.0   # pages scoring below this are written to {stem}_flagged.md
JUDGE_SKIP_THRESHOLD     = 9.0   # pages scoring ≥ this in round 1 are not re-judged in later rounds

# ── Deep review (Phase 9) ───────────────────────────────────────────────────
# gemma4:26b is already loaded as pipeline model — Phase 9 reuses it at no extra cost.
DEEP_REVIEW_MODEL   = _DEEP_REVIEW_OVERRIDE or ORCHESTRATOR_MODEL  # reuses qwen3:14b already loaded from Phase 6 (D49)
DEEP_REVIEW_TIMEOUT = 900              # qwen3:14b mostly in GPU — faster than gemma4:26b CPU+GPU split
DEEP_REVIEW_NUM_CTX = 8192            # needs large ctx: template + 10K raw + 10K md ≈ 3700 tokens min

# ── OCR (D45) ───────────────────────────────────────────────────────────────
OCR_LANG                = "eng"       # Tesseract language code (D22)
OCR_PRIMARY             = "glm-ocr"   # D45 — GLM-OCR: #1 OmniDocBench, document-specialised
OCR_FALLBACK            = "surya"     # D30 — surya fallback when GLM-OCR unavailable
OCR_LAST_RESORT         = "tesseract" # D22 — last resort
GLM_OCR_TIMEOUT         = 60          # seconds per page (GLM-OCR is fast at 2.2 GB)

# ── Math OCR (D35 / D40) ───────────────────────────────────────────────────────
MATH_OCR_ENGINE         = "pix2tex"   # local pix2tex only; set "auto" in .cloak_local.json to enable Mathpix
MATH_OCR_TIMEOUT        = 30          # seconds per equation crop
MATH_FORMULA_THRESHOLD  = 3           # min FormulaItem count across doc to enable math OCR

# ── Mathpix (D40 — opt-in cloud math OCR) ──────────────────────────────────────
# Set MATHPIX_APP_ID and MATHPIX_APP_KEY in .cloak_local.json to activate.
# When not set, pipeline stays fully local (pix2tex or none).
try:
    MATHPIX_APP_ID  = _overrides.get("MATHPIX_APP_ID",  "")
    MATHPIX_APP_KEY = _overrides.get("MATHPIX_APP_KEY", "")
except NameError:
    MATHPIX_APP_ID  = ""
    MATHPIX_APP_KEY = ""
