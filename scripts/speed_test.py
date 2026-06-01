"""
speed_test.py — Benchmark qwen3:8b vs qwen3.6:27b for cloak workloads.

Tests three scenarios that match real cloak phases:
  1. Tool call decision  — short output, agent decides which tool to call
  2. Section patch       — medium output, agent generates 200-token section content
  3. FORMAT pass         — long output, model rewrites a full extracted page

Metrics reported per test:
  - TTFT       : time to first token (prompt eval latency)
  - tok/s      : generation speed (eval tokens/sec)
  - total_time : wall clock seconds
  - output_tok : number of tokens generated

Run: python scripts/speed_test.py
"""

import sys
import time
from pathlib import Path

import ollama

# ── Models to benchmark ────────────────────────────────────────────────────────
MODELS = [
    ("qwen3:8b",      4096),   # current orchestrator
    ("qwen3.6:27b",   8192),   # new candidate — test at 8K ctx
    ("qwen3.6:27b",  16384),   # test at 16K ctx (more KV cache pressure)
    ("qwen3.6:27b",  32768),   # test at 32K ctx (covers most full drafts)
]

# ── Test prompts ───────────────────────────────────────────────────────────────
TESTS = [
    {
        "name": "Tool call decision",
        "desc": "Agent picks the right tool from gap description (short output)",
        "prompt": (
            "You are patching gaps in an extracted PDF document.\n"
            "The quality judge found this gap:\n"
            "- Section 'Results' is missing Table 3 (page 8)\n\n"
            "Document sections: Introduction, Methods, Results, Discussion, Conclusion\n\n"
            "Which tool should you call first to fix this gap? "
            "Call the appropriate tool now."
        ),
        "tools": [
            {"type": "function", "function": {
                "name": "get_page_elements",
                "description": "Return docling element map for a page.",
                "parameters": {"type": "object", "properties": {"page_num": {"type": "integer"}}, "required": ["page_num"]},
            }},
            {"type": "function", "function": {
                "name": "patch_section",
                "description": "Replace a section in the markdown draft.",
                "parameters": {"type": "object", "properties": {"heading": {"type": "string"}, "content": {"type": "string"}}, "required": ["heading", "content"]},
            }},
            {"type": "function", "function": {
                "name": "finish",
                "description": "Signal patching is complete. No arguments.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }},
        ],
    },
    {
        "name": "Section patch generation",
        "desc": "Agent generates ~200 tokens of markdown section content",
        "prompt": (
            "Write a markdown section for 'Methodology' based on this raw page text:\n\n"
            "The study used a randomized controlled design with 240 participants split "
            "into control and treatment groups. Participants were assessed at baseline, "
            "6 months, and 12 months using validated psychometric instruments. "
            "Statistical analysis used mixed-effects models with p<0.05 significance threshold.\n\n"
            "Output the section content as clean markdown (no code fences, no preamble)."
        ),
        "tools": None,
    },
    {
        "name": "FORMAT pass",
        "desc": "Model cleans up a full extracted page (~500 token output)",
        "prompt": (
            "You are formatting extracted PDF text into clean markdown.\n"
            "Fix heading levels, remove duplicate whitespace, and preserve all content.\n\n"
            "Input:\n"
            "## introduction\n"
            "this paper presents a novel approach to thermodynamic cycle analysis. "
            "we examine the carnot cycle efficiency under varying temperature conditions.\n\n"
            "### 1.1 background\n"
            "classical thermodynamics establishes the theoretical maximum efficiency "
            "of a heat engine operating between two thermal reservoirs as:\n"
            "eta = 1 - T_cold / T_hot\n"
            "where T_cold and T_hot are absolute temperatures in kelvin.\n\n"
            "## methodology  \n"
            "experimental setup consisted of a closed-loop system with precision "
            "temperature sensors (±0.1K accuracy) and a calibrated work output meter. "
            "measurements were taken at 10 second intervals over 6 hour test runs.\n\n"
            "### 2.1 data collection\n"
            "raw sensor data was logged to csv format and post-processed using python. "
            "outliers beyond 3 standard deviations were excluded from analysis.\n\n"
            "Output the corrected markdown:"
        ),
        "tools": None,
    },
]


def run_test(model: str, num_ctx: int, test: dict) -> dict:
    """Run a single benchmark test and return metrics."""
    try:
        start = time.monotonic()

        if test["tools"]:
            resp = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": test["prompt"]}],
                tools=test["tools"],
                options={"temperature": 0.1, "num_ctx": num_ctx},
            )
            output_text = resp.message.content or ""
            tool_calls = getattr(resp.message, "tool_calls", None) or []
            output_tok = getattr(resp, "eval_count", None) or len(output_text.split())
            prompt_tok = getattr(resp, "prompt_eval_count", None) or 0
            eval_dur = getattr(resp, "eval_duration", None) or 1
            prompt_dur = getattr(resp, "prompt_eval_duration", None) or 0
            called_tool = tool_calls[0].function.name if tool_calls else "(no tool call)"
        else:
            resp = ollama.generate(
                model=model,
                prompt=test["prompt"],
                options={"temperature": 0.1, "num_ctx": num_ctx},
            )
            output_text = resp.response or ""
            tool_calls = []
            called_tool = None
            output_tok = resp.eval_count or 0
            prompt_tok = resp.prompt_eval_count or 0
            eval_dur = resp.eval_duration or 1
            prompt_dur = resp.prompt_eval_duration or 0

        elapsed = time.monotonic() - start
        tok_s = (output_tok / eval_dur * 1e9) if eval_dur and output_tok else 0
        ttft_s = prompt_dur / 1e9 if prompt_dur else 0

        return {
            "ok": True,
            "ttft_s": round(ttft_s, 2),
            "tok_s": round(tok_s, 1),
            "output_tok": output_tok,
            "prompt_tok": prompt_tok,
            "elapsed_s": round(elapsed, 1),
            "called_tool": called_tool,
            "output_preview": output_text[:120].replace("\n", " "),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    print("\n" + "═" * 70)
    print("  cloak speed test — qwen3:8b vs qwen3.6:27b")
    print("═" * 70)

    for test in TESTS:
        print(f"\n{'─' * 70}")
        print(f"  TEST: {test['name']}")
        print(f"  {test['desc']}")
        print(f"{'─' * 70}")
        print(f"  {'Model':<22} {'ctx':>6}  {'TTFT':>6}  {'tok/s':>7}  {'out_tok':>7}  {'time':>6}  {'tool/preview'}")
        print(f"  {'─'*22} {'─'*6}  {'─'*6}  {'─'*7}  {'─'*7}  {'─'*6}  {'─'*30}")

        for model, num_ctx in MODELS:
            # Skip large ctx for 8b to avoid OOM
            if model == "qwen3:8b" and num_ctx > 4096:
                continue

            sys.stdout.write(f"  {model:<22} {num_ctx:>6}  running...")
            sys.stdout.flush()

            r = run_test(model, num_ctx, test)

            if r["ok"]:
                extra = r["called_tool"] if r["called_tool"] else r["output_preview"]
                print(f"\r  {model:<22} {num_ctx:>6}  {r['ttft_s']:>5.1f}s  {r['tok_s']:>6.1f}/s  "
                      f"{r['output_tok']:>7}  {r['elapsed_s']:>5.1f}s  {str(extra)[:35]}")
            else:
                print(f"\r  {model:<22} {num_ctx:>6}  ERROR: {r['error'][:50]}")

    print(f"\n{'═' * 70}")
    print("  Done. Key metrics for cloak:")
    print("  - TTFT   : latency per agent iteration (lower = faster loop)")
    print("  - tok/s  : generation speed (higher = faster FORMAT/patch)")
    print("  - time   : total wall clock per test")
    print("═" * 70 + "\n")


if __name__ == "__main__":
    main()
