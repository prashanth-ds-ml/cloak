# Question Paper Samples — Notes

## What makes a good question paper test case

A question paper stresses the pipeline because:
- Question numbers use inconsistent formats: 1., Q1., (1), 1a., i.
- Inline equations break pdfplumber text extraction (e.g. `∫f(x)dx`, `H₂O`)
- Options A/B/C/D are often in 2-column layout within the question block
- Marks annotations (e.g. "[3 marks]", "(5)") must be preserved
- Section dividers ("SECTION A — COMPULSORY") must become headings
- Sub-questions must be properly nested (1 → 1a → 1a.i)
- Diagrams and graphs appear inline with questions

## Ideal samples to collect

| File | Source | Type |
|---|---|---|
| IIT JEE Mains paper | jeeadv.ac.in or cbse.gov.in | Engineering entrance, physics/chemistry/math |
| UPSC General Studies | upsc.gov.in | Civil services, text-heavy, essay questions |
| NEET Biology paper | nta.ac.in | Medical entrance, MCQ-heavy |
| Cambridge A-Level Math | cambridgeinternational.org | Equations, structured multi-part questions |
| A university CS exam | Any university — many post online | Code questions, algorithm traces |

## What to check in the output

```
cloak validate data/outputs/question_paper/{name}/final.md
```

- [ ] Question numbers preserved in correct order (not renumbered by model)
- [ ] Each question is a separate block (not merged with the next)
- [ ] Options A/B/C/D on separate lines under each MCQ
- [ ] Section headings (SECTION A, SECTION B) present as ## headings
- [ ] Marks notation preserved (`[3 marks]` not dropped)
- [ ] Equations present (even if imperfect) — not replaced with `[unreadable]`
- [ ] Sub-questions nested correctly under parent question
