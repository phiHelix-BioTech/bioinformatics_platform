"""
Real assessment runner.

Annotation order per variant:
  Batch 1 (parallel): ClinVar, gnomAD, Ensembl VEP, CADD, MyVariant.info, SpliceAI, InterVar
  Batch 2 (sequential, depends on gene/rsid from batch 1):
    CancerHotspots — needs gene name
    dbSNP          — only if rsid still missing after batch 1

Gene-level cache (per unique gene, parallel per new gene):
  10. UniProt   — protein name + function
  11. HGNC      — authoritative gene symbol, locus group, Ensembl/Entrez IDs
  12. ClinGen   — gene-disease validity (Definitive/Strong/Moderate)
  13. GenCC     — aggregated gene-disease validity (ClinGen+OMIM+Orphanet+PanelApp)
  14. HPO       — phenotype terms associated with gene (via Ensembl)
  15. LOVD      — locus-specific variant count
  16. OMIM      — gene-disease + inheritance (optional, needs API key)
  17. Orphanet  — rare disease associations (optional, needs API key)
"""
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.config import settings
from app.services.assessment.base import AssessmentResult, AssessmentRunner
from app.services.assessment.databases import (
    query_cancer_hotspots,
    query_cadd,
    query_clingen,
    query_clinvar,
    query_dbsnp,
    query_ensembl_vep,
    query_gencc,
    query_gnomad,
    query_hgnc,
    query_hpo,
    query_intervar,
    query_lovd,
    query_myvariant,
    query_omim,
    query_orphanet,
    query_spliceai,
    query_uniprot,
)
from app.services.assessment.report import generate_pdf
from app.services.log_streamer import append_log

logger = logging.getLogger(__name__)

_GNOMAD_DATASET = {
    "hg38":   "gnomad_r4",
    "hg19":   "gnomad_r2_1",
    "grch38": "gnomad_r4",
    "grch37": "gnomad_r2_1",
}
_VEP_ASSEMBLY = {
    "hg38":   "GRCh38",
    "hg19":   "GRCh37",
    "grch38": "GRCh38",
    "grch37": "GRCh37",
}
_CADD_GENOME = {
    "gnomad_r4":   "GRCh38-v1.7",
    "gnomad_r2_1": "GRCh37-v1.6",
}
_SPLICEAI_HG = {
    "gnomad_r4":   "38",
    "gnomad_r2_1": "19",
}
_INTERVAR_BUILD = {
    "gnomad_r4":   "hg38",
    "gnomad_r2_1": "hg19",
}

# Maximum number of unique genes held in the per-run gene cache.
# Evicts oldest entry when exceeded.
_GENE_CACHE_MAX = 512

# Redis variant annotation cache — 7 days TTL.
# Caches the merged result of all 7 parallel API calls per variant+genome.
_VARIANT_CACHE_TTL = 7 * 24 * 3600


def _variant_cache_key(chrom: str, pos: Any, ref: str, alt: str, gnomad_ds: str) -> str:
    return f"variant_ann:{chrom}:{pos}:{ref}>{alt}:{gnomad_ds}"


def _get_cached_annotation(key: str) -> dict | None:
    try:
        import redis as _redis
        val = _redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True).get(key)
        return json.loads(val) if val else None
    except Exception:
        return None


def _set_cached_annotation(key: str, data: dict) -> None:
    try:
        import redis as _redis
        _redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True).setex(
            key, _VARIANT_CACHE_TTL, json.dumps(data)
        )
    except Exception:
        pass


class RealAssessmentRunner(AssessmentRunner):
    def run(
        self,
        job_id: str,
        variants: list[dict],
        workflow_config: dict | None = None,
    ) -> AssessmentResult:
        genome         = (workflow_config or {}).get("genome", settings.ASSESSMENT_GENOME).lower()
        gnomad_ds      = _GNOMAD_DATASET.get(genome, "gnomad_r4")
        vep_assembly   = _VEP_ASSEMBLY.get(genome, "GRCh38")
        cadd_genome    = _CADD_GENOME.get(gnomad_ds, "GRCh38-v1.7")
        spliceai_hg    = _SPLICEAI_HG.get(gnomad_ds, "38")
        intervar_build = _INTERVAR_BUILD.get(gnomad_ds, "hg38")

        append_log(
            job_id,
            f"Starting annotation of {len(variants)} variant(s) "
            f"[genome={genome}, gnomAD={gnomad_ds}, "
            f"OMIM={'yes' if settings.OMIM_API_KEY else 'no key'}, "
            f"Orphanet={'yes' if settings.ORPHANET_API_KEY else 'no key'}]",
        )

        # Gene-level cache — shared across all variants in this job run.
        _gene_cache: dict[str, dict[str, Any]] = {}

        annotated: list[dict] = []

        for i, v in enumerate(variants):
            ann = dict(v)
            chrom = v.get("chrom", "")
            pos   = v.get("pos", 0)
            ref   = v.get("ref", "")
            alt   = v.get("alt", "")
            label = f"[{i+1}/{len(variants)}] {chrom}:{pos} {ref}>{alt}"

            cache_key = _variant_cache_key(chrom, pos, ref, alt, gnomad_ds)
            cached = _get_cached_annotation(cache_key)

            if cached:
                append_log(job_id, f"  {label} → cache hit")
                cv  = cached.get("cv",  {})
                gn  = cached.get("gn",  {})
                vep = cached.get("vep", {})
                cad = cached.get("cad", {})
                mv  = cached.get("mv",  {})
                sai = cached.get("sai", {})
                iv  = cached.get("iv",  {})
            else:
                append_log(job_id, f"  {label} → ClinVar / gnomAD / VEP / CADD / MyVariant / SpliceAI / InterVar (parallel)")

                # ── Batch 1: 7 independent variant-level queries in parallel ─────────
                with ThreadPoolExecutor(max_workers=7) as ex:
                    f_cv  = ex.submit(query_clinvar,      chrom, pos, ref, alt)
                    f_gn  = ex.submit(query_gnomad,       chrom, pos, ref, alt, gnomad_ds)
                    f_vep = ex.submit(query_ensembl_vep,  chrom, pos, ref, alt, vep_assembly)
                    f_cad = ex.submit(query_cadd,         chrom, pos, ref, alt, cadd_genome)
                    f_mv  = ex.submit(query_myvariant,    chrom, pos, ref, alt)
                    f_sai = ex.submit(query_spliceai,     chrom, pos, ref, alt, spliceai_hg)
                    f_iv  = ex.submit(query_intervar,     chrom, pos, ref, alt, intervar_build)

                cv  = f_cv.result()
                gn  = f_gn.result()
                vep = f_vep.result()
                cad = f_cad.result()
                mv  = f_mv.result()
                sai = f_sai.result()
                iv  = f_iv.result()
                _set_cached_annotation(cache_key, {"cv": cv, "gn": gn, "vep": vep,
                                                    "cad": cad, "mv": mv, "sai": sai, "iv": iv})

            # 1. ClinVar
            ann["significance"] = cv.get("significance", "Unknown")
            ann["gene"]         = cv.get("gene", v.get("gene", ""))
            ann["hgvs"]         = cv.get("hgvs", "")
            ann["rsid"]         = cv.get("rsid", v.get("id", "."))

            # 2. gnomAD
            if gn.get("af") is not None:
                ann["af"] = gn["af"]
            if gn.get("af_popmax") is not None:
                ann["af_popmax"] = gn["af_popmax"]
            if not ann["gene"] and gn.get("gene"):
                ann["gene"] = gn["gene"]
            if ann["rsid"] in (".", "", None) and gn.get("rsids"):
                ann["rsid"] = gn["rsids"][0]
            if not ann.get("hgvs"):
                ann["hgvs"] = gn.get("hgvsc") or gn.get("hgvsp") or ""

            # 3. Ensembl VEP
            ann["sift_score"]     = vep.get("sift_score")
            ann["sift_pred"]      = vep.get("sift_pred", "")
            ann["polyphen_score"] = vep.get("polyphen_score")
            ann["polyphen_pred"]  = vep.get("polyphen_pred", "")
            ann["consequence"]    = vep.get("consequence", "")
            ann["transcript"]     = vep.get("transcript", "")
            if not ann["gene"] and vep.get("gene"):
                ann["gene"] = vep["gene"]
            if not ann.get("hgvs") and vep.get("hgvsc"):
                ann["hgvs"] = vep["hgvsc"]

            # 4. CADD
            ann["cadd_phred"] = cad.get("cadd_phred")
            ann["cadd_raw"]   = cad.get("cadd_raw")

            # 5. MyVariant.info
            ann["revel"]                 = mv.get("revel")
            ann["metalr"]                = mv.get("metalr")
            ann["metasvm"]               = mv.get("metasvm")
            ann["mutation_taster_pred"]  = mv.get("mutation_taster_pred", "")
            ann["mutation_taster_score"] = mv.get("mutation_taster_score")
            ann["gerp_rs"]               = mv.get("gerp_rs")
            ann["phylop"]                = mv.get("phylop")

            # 6. SpliceAI
            ann["spliceai_ds_max"] = sai.get("spliceai_ds_max")
            ann["spliceai_ds_ag"]  = sai.get("spliceai_ds_ag")
            ann["spliceai_ds_al"]  = sai.get("spliceai_ds_al")
            ann["spliceai_ds_dg"]  = sai.get("spliceai_ds_dg")
            ann["spliceai_ds_dl"]  = sai.get("spliceai_ds_dl")

            # 7. InterVar
            ann["intervar_class"] = iv.get("intervar_class", "")
            ann["acmg_criteria"]  = iv.get("acmg_criteria", [])

            # ── Batch 2: gene-dependent queries (sequential, cheap) ───────────────
            # 8. CancerHotspots — needs gene from batch 1
            if ann["gene"]:
                hs = query_cancer_hotspots(ann["gene"])
                ann["hotspot"]      = hs.get("is_hotspot", False)
                ann["hotspot_type"] = hs.get("hotspot_type")
            else:
                ann["hotspot"] = False

            # 9. dbSNP — rsID last resort, only if still missing
            if ann["rsid"] in (".", "", None):
                snp = query_dbsnp(chrom, pos)
                ann["rsid"] = snp.get("rsid", ".")

            # ── Gene-level cache (parallel per unique gene) ───────────────────────
            gene = ann.get("gene", "")
            if gene and gene not in _gene_cache:
                append_log(
                    job_id,
                    f"  gene={gene} → UniProt / HGNC / ClinGen / GenCC / HPO / LOVD"
                    + (" / OMIM" if settings.OMIM_API_KEY else "")
                    + (" / Orphanet" if settings.ORPHANET_API_KEY else ""),
                )
                with ThreadPoolExecutor(max_workers=8) as ex:
                    f_uni    = ex.submit(query_uniprot,  gene)
                    f_hgnc   = ex.submit(query_hgnc,     gene)
                    f_clingen = ex.submit(query_clingen, gene)
                    f_gencc  = ex.submit(query_gencc,    gene)
                    f_hpo    = ex.submit(query_hpo,      gene)
                    f_lovd   = ex.submit(query_lovd,     gene)
                    f_omim   = ex.submit(query_omim,     gene)
                    f_orpha  = ex.submit(query_orphanet, gene)

                gene_data: dict[str, Any] = {}
                for f in (f_uni, f_hgnc, f_clingen, f_gencc, f_hpo, f_lovd, f_omim, f_orpha):
                    gene_data.update(f.result())

                # Evict oldest entry if cache is at capacity
                if len(_gene_cache) >= _GENE_CACHE_MAX:
                    _gene_cache.pop(next(iter(_gene_cache)))
                _gene_cache[gene] = gene_data

            if gene and gene in _gene_cache:
                for k, val in _gene_cache[gene].items():
                    ann.setdefault(k, val)

            # AF from VCF INFO field if gnomAD returned nothing
            ann.setdefault("af", _parse_af(str(v.get("info", ""))))

            annotated.append(ann)

        # Generate PDF report
        output_path = os.path.join(settings.UPLOADS_DIR, f"assessment-{job_id}.pdf")
        append_log(job_id, f"Generating PDF report → {output_path}")
        report_path: str | None = None
        report_sha256: str | None = None
        try:
            report_path, report_sha256 = generate_pdf(job_id, annotated, output_path)
            append_log(job_id, f"PDF written — SHA-256: {report_sha256}")
        except Exception as exc:
            logger.error("[assessment] PDF generation failed for %s: %s", job_id, exc)
            append_log(job_id, f"WARNING: PDF generation failed — {exc}")

        pathogenic    = sum(1 for a in annotated if "pathogenic" in a.get("significance", "").lower())
        hotspot_count = sum(1 for a in annotated if a.get("hotspot"))

        summary = (
            f"{len(annotated)} variants assessed. "
            f"{pathogenic} pathogenic/likely pathogenic. "
            f"{hotspot_count} cancer hotspot(s) found."
        )
        append_log(job_id, f"Assessment complete: {summary}")

        return AssessmentResult(
            summary=summary,
            variants=annotated,
            report_path=report_path,
            report_sha256=report_sha256,
        )


def _parse_af(info: str) -> float | None:
    for part in info.split(";"):
        if part.upper().startswith("AF="):
            try:
                return float(part.split("=", 1)[1])
            except ValueError:
                pass
    return None
