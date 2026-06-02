"""
download_icmr_stws.py -- download all ICMR STW PDFs from icmr.gov.in.

Downloads all 78+ individual STW documents (excludes "_all" compilation PDFs).
Saves to data/samples/icmr_stw_full/ organized by specialty.

Usage: python scripts/download_icmr_stws.py
"""
from __future__ import annotations
import sys, io, time, json
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── All ICMR STW PDFs ─────────────────────────────────────────────────────────
# Source: https://www.icmr.gov.in/standard-treatment-workflows-stws
# Excludes _all compilation PDFs — individual condition documents only

BASE = "https://www.icmr.gov.in/icmrobject/uploads/STWs/"

STWS = {
    "hypertension": [
        ("hypertension_in_adults", "1778941063_hypertensioninadults_final.pdf"),
    ],
    "cardiology": [
        ("cardiology_af",              "1768823343_cardiology_1-1.pdf"),
        ("cardiology_bradyarrhythmia", "1771492063_bradyarrthymia13_2_2.pdf"),
        ("cardiology_heart_failure",   "1768823345_cardiology_1-3.pdf"),
        ("cardiology_nstemi",          "1768823342_cardiology_1-4.pdf"),
        ("cardiology_stable_angina",   "1768823342_cardiology_1-6.pdf"),
        ("cardiology_stemi",           "1768823342_cardiology_1-5.pdf"),
    ],
    "ent": [
        ("ent_acute_rhinosinusitis",          "1725952292_ent_acute_rhinosinusitis.pdf"),
        ("ent_chronic_rhinosinusitis",         "1725952351_ent_chronic_rhinosinusitis.pdf"),
        ("ent_epistaxis",                      "1725952351_ent_epistaxis.pdf"),
        ("ent_hearing_impairment",             "1725952350_ent_hearing_impairment.pdf"),
        ("ent_neck_space_infection",           "1725952350_ent_neck_space_infection.pdf"),
        ("ent_otorrhoea",                      "1725952350_ent_otorrhoea.pdf"),
        ("ent_pharyngitis_sore_throat",        "1725952349_ent_pharyngitis_and_sore_throat.pdf"),
    ],
    "nephrology": [
        ("nephrology_acute_kidney_injury",   "1725952348_nephrology_acute_kidney_injury.pdf"),
        ("nephrology_chronic_kidney_disease","1725952348_nephrology_chronic_kidney_disease.pdf"),
        ("nephrology_glomerulonephritis",    "1725952348_nephrology_glomerulonephritis.pdf"),
        ("nephrology_uti",                   "1725952348_nephrology_urinary_tract_infection.pdf"),
    ],
    "neurology": [
        ("neurology_acute_paralysis", "1725952346_neurology_acute_paralysis.pdf"),
        ("neurology_dementia",        "1725952345_neurology_dementia.pdf"),
        ("neurology_epilepsy",        "1725952345_neurology_epilepsy.pdf"),
        ("neurology_headache",        "1725952344_neurology_headache.pdf"),
        ("neurology_neuroinfections", "1725952343_neurology_neuroinfections.pdf"),
        ("neurology_stroke",          "1725952342_neurology_stroke.pdf"),
    ],
    "obgyn": [
        ("obgyn_antenatal",            "1771568619_ante-natalmanagementofnormalpregnancy-7.pdf"),
        ("obgyn_dc",                   "1768382529_updateddc.pdf"),
        ("obgyn_fibroids_polyps",      "1768382529_updateduterinefibroidsandpolyps.pdf"),
        ("obgyn_heavy_menstrual",      "1768382529_updatedheavymenstrualbleeding.pdf"),
        ("obgyn_hysterectomy",         "1768382528_updatedhysterectomyforbenigngynaecologicalconditions.pdf"),
        ("obgyn_postpartum_haem",      "1768382528_updatedpostpartumhaemorrhage.pdf"),
    ],
    "paediatrics": [
        ("paediatrics_encephalitis",    "1725959608_paediatrics_acute_encephalitis_syndrome.pdf"),
        ("paediatrics_dengue",          "1725952338_paediatrics_dengue_fever.pdf"),
        ("paediatrics_diarrhea",        "1725952338_paediatrics_diarrhea.pdf"),
        ("paediatrics_fever",           "1725952338_paediatrics_fever_in_children.pdf"),
        ("paediatrics_sepsis",          "1725952338_paediatrics_sepsis_and_septic_shock_in_children.pdf"),
        ("paediatrics_malnutrition",    "1725952337_paediatrics_severe_acute_malnutrition.pdf"),
        ("paediatrics_pneumonia",       "1725952336_paediatrics_severe_pneumonia.pdf"),
    ],
    "psychiatry": [
        ("psychiatry_alcohol",           "1725952335_psychiatry_alcohol_use_disorders.pdf"),
        ("psychiatry_anxiety_ocd",       "1725952333_psychiatry_anxiety_disorder.pdf"),
        ("psychiatry_child_behavioral",  "1725952332_psychiatry_child_behavioral_disorder.pdf"),
        ("psychiatry_developmental",     "1725952333_psychiatry_developmental_problems.pdf"),
        ("psychiatry_emotional",         "1725952332_psychiatry_childhood_emotional_disorders.pdf"),
        ("psychiatry_depression",        "1725952332_psychiatry_depression.pdf"),
        ("psychiatry_psychosis",         "1725952331_psychiatry_psychosis.pdf"),
        ("psychiatry_somatoform",        "1725952330_psychiatry_somatoform_disorder.pdf"),
    ],
    "pulmonology": [
        ("pulmonology_ari",   "1725963734_pulmonology_acute_respiratory_infections.pdf"),
        ("pulmonology_asthma","1725952329_pulmonology_asthma.pdf"),
        ("pulmonology_copd",  "1725952329_pulmonology_chronic_obstructive_pulmonary_disease.pdf"),
        ("pulmonology_resp_failure","1725952328_pulmonology_respiratory_failure.pdf"),
    ],
    "urology": [
        ("urology_acute_urinary_retention","1768800257_acuteurinaryretentioninmen3.pdf"),
        ("urology_gross_haematuria",       "1768800256_grosshaematuria-2.pdf"),
        ("urology_male_infertility",       "1765885318_maleinfertility2.pdf"),
        ("urology_renal_ureteric_stones",  "1768800256_renalanduretericstones3.pdf"),
        ("urology_scrotal_swelling",       "1768800255_scrotalswelling3.pdf"),
    ],
    "tb_adult": [
        ("tb_abdominal",         "1725964690_1_adult_abdominal_tb_18032022.pdf"),
        ("tb_lymphadenopathy",   "1725964690_2_adult_lymphadenopathy_18032022.pdf"),
        ("tb_musculoskeletal",   "1725964689_3_eptb-musculoskeletal_18032022.pdf"),
        ("tb_pericardial",       "1725964689_4_pericardial_tb_15032022.pdf"),
        ("tb_pleural",           "1725964689_5_plural_tb_18032022.pdf"),
        ("tb_meningitis",        "1725964688_6_adult_tbm_21032022.pdf"),
        ("tb_skin",              "1725964688_7_skin_tuberculosis_18032022.pdf"),
        ("tb_female_genital",    "1725964688_8_fgtm_15032022.pdf"),
        ("tb_genitourinary",     "1725964688_9_genitourinary_tuberculosis_16032022.pdf"),
        ("tb_intraocular",       "1725964687_10_intraocular_tuberculosis_15032022.pdf"),
    ],
    "tb_investigations": [
        ("tb_drug_dosages",   "1725964686_2_ntep_18032022.pdf"),
        ("tb_hepatitis",      "1725964686_3_antitubercular_therapy_hepatitis_18032022.pdf"),
        ("tb_microbiology",   "1725964685_1_microbiological_18032022.pdf"),
    ],
    "tb_paediatric": [
        ("tb_paed_abdominal",     "1725964684_1_paediatric_abdominal_tb.pdf"),
        ("tb_paed_intrathoracic", "1725964684_2_paediatric_intrathoracis_tb.pdf"),
        ("tb_paed_lymphnode",     "1725964682_3_paediatric_ln_tb.pdf"),
        ("tb_paed_osteoarticular","1725964681_4_paediatric_osteoarticular_tb.pdf"),
        ("tb_paed_meningitis",    "1725964680_5_paediatric_tubercular_meningitis.pdf"),
    ],
    "dermatology": [
        ("derm_acne_rosacea",      "1725967368_acne_roseacea.pdf"),
        ("derm_alopecia",          "1725967368_alopecia.pdf"),
        ("derm_bacterial",         "1725967368_bacterial_skin_infections.pdf"),
        ("derm_cadr_a",            "1725967367_cutaneous_part_a.pdf"),
        ("derm_cadr_b",            "1725967367_cutaneous_part_b.pdf"),
        ("derm_dermatophytosis",   "1725967367_dermatophytoses.pdf"),
        ("derm_eczema",            "1725967367_eczema.pdf"),
        ("derm_immunobullous",     "1725967366_immunobullous.pdf"),
        ("derm_psoriasis",         "1725967366_psoriasis.pdf"),
        ("derm_topical_steroids",  "1725967365_rational_use_of_topical_medications.pdf"),
        ("derm_scabies",           "1725967365_scabies.pdf"),
        ("derm_urticaria",         "1725967365_urticaria_and_angioedema.pdf"),
        ("derm_varicella_herpes",  "1725967364_varicella__herpes_zoster.pdf"),
        ("derm_vitiligo",          "1725967363_vitiligo.pdf"),
    ],
    "endocrinology": [
        ("endo_diabetes_t1",    "1726567249_diabetes_mellitus_type_1.pdf"),
        ("endo_diabetes_t2",    "1726567245_diabetes_mellitus_type_2.pdf"),
        ("endo_dka",            "1726567244_diabetic_ketoacidosis.pdf"),
        ("endo_fragility",      "1726567244_fragility_fractures.pdf"),
        ("endo_hyponatremia",   "1726567243_approach_to_hyponatremia.pdf"),
        ("endo_hypothyroid",    "1726567243_hypothyroidism.pdf"),
    ],
    "gastroenterology": [
        ("gastro_gi_bleed_a",  "1726567564_gastrointestinal_bleed_part_a.pdf"),
        ("gastro_gi_bleed_b",  "1726567564_gastrointestinal_bleed_part_b.pdf"),
        ("gastro_jaundice",    "1726567564_jaundice.pdf"),
        ("gastro_liver_fail",  "1726567562_liver_failure.pdf"),
    ],
    "general_surgery": [
        ("surg_appendicitis",  "1726567743_appendicitis.pdf"),
        ("surg_cbd_stone",     "1726567743_common_bile_duct_stone.pdf"),
        ("surg_diabetic_foot", "1726567742_diabetic_foot.pdf"),
        ("surg_gallstone",     "1726567742_gall_stone_disease.pdf"),
        ("surg_hernia",        "1726567741_ventral_hernia.pdf"),
    ],
    "haematology": [
        ("haem_sickle_cell",   "1726568229_sickle_cell_disease.pdf"),
    ],
    "infertility": [
        ("infert_female",      "1726568410_female_infertility.pdf"),
    ],
    "neonatology": [
        ("neo_feeds_fluids",   "1726568622_feeds_fluids_in_neonates.pdf"),
        ("neo_thermal_care",   "1726568617_thermal_care_newborn.pdf"),
        ("neo_triage",         "1726568620_neonatal_emergency_triage.pdf"),
        ("neo_hypoglycemia",   "1726568620_neonatal_hypoglycemia.pdf"),
        ("neo_jaundice",       "1726568620_neonatal_jaundice.pdf"),
        ("neo_seizures",       "1726568620_neonatal_seizures.pdf"),
        ("neo_transport",      "1726568619_neonatal_transport.pdf"),
        ("neo_post_asphyxia",  "1726568619_post_asphyxial.pdf"),
        ("neo_resp_distress",  "1726568619_respiratory_distress.pdf"),
        ("neo_sepsis",         "1726568618_sepsis_neonates.pdf"),
    ],
    "oncology": [
        ("onco_breast",       "1726568905_breast_cancer.pdf"),
        ("onco_oral_lip",     "1726568903_lip_and_oral_cancer.pdf"),
        ("onco_lung",         "1726568902_lung_cancer.pdf"),
    ],
    "ophthalmology": [
        ("ophthal_cataract",    "1726569103_cataract.pdf"),
        ("ophthal_diabetic_ret","1726569102_diabetic_retinopathy.pdf"),
        ("ophthal_glaucoma",    "1726569102_glaucoma.pdf"),
    ],
    "orthopaedics": [
        ("ortho_septic_arthritis",    "1728474671_septic.pdf"),
        ("ortho_supracondylar",       "1728474669_supracondylar_fracture.pdf"),
        ("ortho_ankle_fractures",     "1726645316_1_ankle_fractures.pdf"),
        ("ortho_distal_femur",        "1726645316_2_distal_femur_fractures.pdf"),
        ("ortho_distal_radius",       "1726645316_3_fracture_distal_end_radius.pdf"),
        ("ortho_neck_femur",          "1726645315_4_fracture_neck_of_femur.pdf"),
        ("ortho_hip_oa",              "1726645315_5_hip_oa.pdf"),
        ("ortho_intertrochanteric",   "1726645314_6_intertrochanteric_femoral_fractures.pdf"),
        ("ortho_low_back_pain",       "1726645314_7_low_back_pain.pdf"),
        ("ortho_neck_pain",           "1726645314_8_neck_pain.pdf"),
        ("ortho_open_fracture",       "1726645314_9_open_fracture.pdf"),
        ("ortho_knee_oa",             "1726645313_10_osteoarthritis.pdf"),
        ("ortho_tibial_plateau",      "1742815718_tibialplateaufractures.pdf"),
    ],
    "paed_surgery": [
        ("paed_surg_scrotum",       "1726569369_acute_scrotum_in_children.pdf"),
        ("paed_surg_hernia",        "1726569368_congenital_inguinal_hernias.pdf"),
        ("paed_surg_constipation",  "1726569368_constipation.pdf"),
        ("paed_surg_empyema",       "1726569368_empyema_thoracis.pdf"),
        ("paed_surg_undescended",   "1726569367_undescended_testis.pdf"),
    ],
    "ctvs": [
        ("ctvs_aortic_syndrome",  "1726643579_1_acute_aortic_syndrome.pdf"),
        ("ctvs_limb_ischemia",    "1726643577_2_acute_limb_ischemia.pdf"),
        ("ctvs_chest_trauma",     "1726643578_3_chest_trauma.pdf"),
        ("ctvs_chronic_limb",     "1726643577_4_chronic_lower_limb_ischemia.pdf"),
        ("ctvs_cad_surgery",      "1726643576_5_surgery_for_cad.pdf"),
    ],
    "interventional_radiology": [
        ("ir_abdominal_abscess",  "1726644030_1_image_guided_drainage_of_intra_abdominal_abscess.pdf"),
        ("ir_haemoptysis",        "1726644027_2_image_guided_management_of_haemoptysis.pdf"),
        ("ir_liver_tumors",       "1726644027_3_therapies_for_primary_liver_tumors.pdf"),
        ("ir_obstructive_jaundice","1726644019_4_image_guided_management_of_obstructive_jaundice.pdf"),
        ("ir_stroke",             "1726644021_5_image_guided_management_of_stroke.pdf"),
        ("ir_vaginal_bleeding",   "1726644012_6_image_guided_management_of_vaginal_bleeding.pdf"),
        ("ir_varicose_veins",     "1726644017_7_image_guided_management_of_varicose_veins.pdf"),
    ],
    "neurosurgery": [
        ("neurosurg_brain_tumor", "1726645069_1_brain_tumors.pdf"),
        ("neurosurg_head_injury", "1726645069_2_head_injury.pdf"),
        ("neurosurg_spinal",      "1726645068_3_spinal_injury.pdf"),
    ],
    "paed_cardiology": [
        ("paed_card_rheumatic",   "1726651147_1_acute_rheumatic_fever.pdf"),
        ("paed_card_critical",    "1726651147_2_critical_heart_disease_in_the_newborn.pdf"),
        ("paed_card_heart_fail",  "1726651147_3_pediatric_heart_failure.pdf"),
        ("paed_card_kawasaki",    "1726651146_4_kawasaki_disease_new.pdf"),
        ("paed_card_shunt",       "1726651145_5_left_to_right_shunt_lesions.pdf"),
        ("paed_card_tachyarr",    "1726651144_6_tachyarrhythmia.pdf"),
    ],
}


def download_all(output_dir: Path, delay: float = 0.5) -> dict:
    """Download all STW PDFs. Returns {name: status} dict."""
    import httpx

    output_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    total = sum(len(v) for v in STWS.values())
    done = 0

    print(f"Downloading {total} STW PDFs to {output_dir}/\n")

    for specialty, docs in STWS.items():
        spec_dir = output_dir / specialty
        spec_dir.mkdir(exist_ok=True)

        for name, filename in docs:
            done += 1
            url = BASE + filename
            out_path = spec_dir / f"{name}.pdf"

            if out_path.exists() and out_path.stat().st_size > 1000:
                print(f"  [{done:3d}/{total}] SKIP (exists)  {name}")
                results[name] = "exists"
                continue

            try:
                print(f"  [{done:3d}/{total}] GET  {name} ...", end=" ", flush=True)
                resp = httpx.get(url, timeout=30, follow_redirects=True)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    out_path.write_bytes(resp.content)
                    kb = len(resp.content) // 1024
                    print(f"{kb} KB ✓")
                    results[name] = "ok"
                else:
                    print(f"HTTP {resp.status_code} ✗")
                    results[name] = f"error:{resp.status_code}"
            except Exception as exc:
                print(f"FAILED: {exc} ✗")
                results[name] = f"error:{exc}"

            time.sleep(delay)

    return results


def main():
    output_dir = Path("data/samples/icmr_stw_full")
    results = download_all(output_dir, delay=0.3)

    ok = sum(1 for v in results.values() if v in ("ok", "exists"))
    fail = sum(1 for v in results.values() if v.startswith("error"))

    print(f"\n{'='*60}")
    print(f"Downloaded: {ok} success  {fail} failed")
    print(f"Location:   {output_dir}/")

    if fail > 0:
        print("\nFailed downloads:")
        for name, status in results.items():
            if status.startswith("error"):
                print(f"  {name}: {status}")

    # Save manifest
    manifest = {"results": results, "total": len(results), "ok": ok, "failed": fail}
    Path("data/batch_logs/download_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"\nManifest: data/batch_logs/download_manifest.json")


if __name__ == "__main__":
    main()
