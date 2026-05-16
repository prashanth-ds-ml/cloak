from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR     = PROJECT_ROOT / "data"
RAW_DIR      = DATA_DIR / "raw"
MD_DIR       = DATA_DIR / "markdown"
IMG_DIR      = DATA_DIR / "images"

# ── Ollama models ──────────────────────────────────────────────────────────
ORCHESTRATOR_MODEL  = "qwen3:8b"          # tool-calling, planning
VISION_PRIMARY      = "qwen2.5vl:7b"      # OCR, layout, quality judge
VISION_FALLBACK     = "qwen3-vl:4b"           # 3.3 GB — lighter fallback, same VL family

OLLAMA_BASE_URL     = "http://localhost:11434"

# ── Agentic parser knobs ───────────────────────────────────────────────────
MAX_ROUNDS          = 4        # max extract→judge→patch iterations
QUALITY_THRESHOLD   = 8.0      # stop early if score ≥ this
CONTEXT_TOKEN_LIMIT = 8_000    # compress history above this
CONTENT_LOSS_LIMIT  = 0.35     # revert patch if >35% chars removed

# ── Vision / rendering ─────────────────────────────────────────────────────
PAGE_DPI            = 150      # PNG render resolution
VISION_TIMEOUT      = 400      # seconds per vision call (D18 — raised from 180 for slow GPU)
AGENT_TIMEOUT       = 150      # seconds per orchestrator call
MAX_AGENT_ITERS     = 10       # ReAct loop cap

# ── Image filtering ────────────────────────────────────────────────────────
MIN_IMAGE_BYTES     = 5_000    # ignore images smaller than this

MODEL_NUM_CTX       = 4096     # Ollama context window — lower saves VRAM
FORMAT_NUM_CTX      = 8192     # larger context for Phase 4 FORMAT — avoids qwen3 thinking tokens truncating output
MAX_IMAGE_PX        = 1024     # long-edge cap before sending image to VLM
MODEL_KEEP_ALIVE    = 0        # keep_alive=0 — explicit phase-based unloads handle lifecycle (D11)

# ── System / hardware gate ──────────────────────────────────────────────────
MIN_FREE_RAM_GB         = 9.0   # minimum free RAM to enable vision model (D18)

# ── Page profiler thresholds ────────────────────────────────────────────────
SCANNED_TEXT_THRESHOLD  = 100   # chars below which a page is considered scanned (D21)
IMAGE_AREA_THRESHOLD    = 0.4   # image area ratio above which a page is image_heavy (D21)

# ── Deep review (Phase 9) ───────────────────────────────────────────────────
# Runs after pipeline models are unloaded. Larger model, Ollama handles CPU+GPU split.
DEEP_REVIEW_MODEL   = "gemma4:latest"  # 9.6 GB — uses CPU+GPU split after teardown
DEEP_REVIEW_TIMEOUT = 600              # 10 min — CPU+GPU split is slower

# ── OCR ─────────────────────────────────────────────────────────────────────
OCR_LANG                = "eng" # Tesseract language code (D22)
