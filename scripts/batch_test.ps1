param([switch]$DryRun)

$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

& "$root\.venv\Scripts\Activate.ps1"

$pdfs = @(
    # ── DONE (1–9, paused 2026-05-21) ──────────────────────────────────────
    # neurology_stroke          7.6/10  88% words  5.7 min
    # oncology_breast           8.3/10  88% words  8.1 min
    # ortho_low_back_pain       8.3/10  88% words  6.4 min
    # tb_adult_abdominal        7.9/10  35% words  18.2 min  (flowchart)
    # psychiatry_depression     8.6/10  89% words  4.7 min
    # paediatrics_dengue        8.3/10  22% words  4.1 min   (image-heavy poster)
    # un_civil_political_rights FAILED  corrupt PDF
    # cdc_mmwr_2024_report      text-only  97% words  0.2 min
    # who_covid_situation_report_1  8.8/10  100% words  15.5 min
    # ── RESUME FROM HERE ────────────────────────────────────────────────────
    # Research papers (15–16 pages)
    "data\samples\research_paper\bert_devlin_2018.pdf",
    "data\samples\research_paper\attention_is_all_you_need.pdf",
    # Multi-column survey (11 pages)
    "data\samples\multi_column\arxiv_survey_multi_column.pdf",
    # Table heavy (10 + 18 pages)
    "data\samples\table_heavy\cdc_nchs_body_measurements_codebook_sliced.pdf",
    "data\samples\table_heavy\nhanes_survey_contents.pdf",
    # Question papers — sliced to 10 pages (math heavy)
    "data\samples\question_paper\jee_advanced_2023_paper1_sliced.pdf",
    "data\samples\question_paper\jee_advanced_2023_paper2_sliced.pdf",
    # Legal — sliced to 10 pages
    "data\samples\legal_document\scotus_dobbs_opinion_sliced.pdf",
    "data\samples\legal_document\us_consolidated_appropriations_act_2020_sliced.pdf",
    # Financial — sliced to 10 pages
    "data\samples\financial_report\berkshire_hathaway_2023_annual_report_sliced.pdf",
    # Government large — sliced to 10 pages
    "data\samples\government_document\irs_publication_17_sliced.pdf",
    # Technical / textbook — sliced to 5–10 pages
    "data\samples\technical_manual\postgresql_15_docs_sliced.pdf",
    "data\samples\textbook\engineering_thermodynamics_pk_nag_sliced.pdf",
    # Slide deck — sliced to 10 pages
    "data\samples\slide_deck\mit_ocw_computational_biology_lecture1_sliced.pdf",
    # Scanned — sliced to 5 pages (OCR, slow)
    "data\samples\scanned_pdf\history_dumfries_1800s_scanned_sliced.pdf",
    # Image heavy — 21 pages (full doc)
    "data\samples\image_heavy\nasa_esto_annual_report_2024.pdf"
)

$logDir = "$root\data\batch_logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$total = $pdfs.Count
$idx   = 0

foreach ($pdf in $pdfs) {
    $idx++
    $name = [System.IO.Path]::GetFileNameWithoutExtension($pdf)
    $log  = "$logDir\$name.log"

    Write-Host ""
    Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  [$idx/$total]  $name" -ForegroundColor Cyan
    Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan

    if ($DryRun) {
        Write-Host "  [dry-run] would run: cloak parse $pdf --no-review" -ForegroundColor Yellow
        continue
    }

    $start = Get-Date
    cloak parse $pdf --no-review 2>&1 | Tee-Object -FilePath $log
    $exit = $LASTEXITCODE
    $elapsed = [math]::Round(((Get-Date) - $start).TotalMinutes, 1)

    $status = if ($exit -eq 0) { "OK" } else { "FAILED (exit $exit)" }
    Write-Host ""
    Write-Host "  [$idx/$total]  $name  ->  $status  ($elapsed min)" -ForegroundColor $(if ($exit -eq 0) { 'Green' } else { 'Red' })
    Add-Content -Path "$logDir\summary.log" -Value "$(Get-Date -Format 'HH:mm')  [$status]  $name  ($elapsed min)"
}

Write-Host ""
Write-Host "════ Batch complete ════" -ForegroundColor Green
Get-Content "$logDir\summary.log"
