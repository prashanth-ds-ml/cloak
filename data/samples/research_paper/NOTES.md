# Research Paper Samples — Notes

## What makes a good research paper test case

Academic papers test:
- 2-column layout (most journals and arXiv CS/physics papers)
- Abstract section (should become first body paragraph under H1)
- Section hierarchy: Introduction → Methods → Results → Discussion → Conclusion
- In-text citations [1] and References section at end
- Equations (FormulaItem in docling — may need vision fallback)
- Figures with captions below them
- Tables with captions above them (opposite to figure convention)
- Footnotes in main text

## Ideal samples

| File | Source | Why |
|---|---|---|
| arXiv ML paper (transformer, BERT, etc.) | arxiv.org/pdf/... | 2-column, equations, large figures |
| Nature or PLOS ONE biology paper | plos.org (open access) | Multi-figure, complex tables |
| Math paper with heavy notation | arxiv.org math.* | Equation density extreme |
| Survey/review paper | arxiv.org | Very long references section |

## Good arXiv papers to use (all open access)

- 1706.03762 — "Attention Is All You Need" (transformers) — 2-col, equations, architecture diagrams
- 2005.14165 — "Language Models are Few-Shot Learners" (GPT-3) — many tables, few-shot examples
- 1810.04805 — "BERT" — 2-col, tables, multiple figures

Direct URL format: `https://arxiv.org/pdf/{paper_id}.pdf`

## What to check in output

- [ ] Abstract present as first section (not merged with introduction)
- [ ] Section headings at correct levels (##, ###)
- [ ] References section preserved at end (not truncated)
- [ ] Figures embedded as `![description](path)` with caption on next line
- [ ] Tables have correct column counts
- [ ] Citation numbers [1] preserved inline
