"""
PDF report generator for the Mutation Assessment pipeline.
Uses reportlab (pure Python, no browser dependency).

Layout:
  - Overall page size: landscape A4 (297 x 210 mm)
  - Table A — Clinical Summary        (portrait-width within landscape page)
  - Table B — Computational Scores    (landscape, separate page)
  - Table C — Gene Disease Associations (landscape, separate page)
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    HRFlowable,
    PageBreak,
)
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing

# ── Significance colours ───────────────────────────────────────────────────

_SIG_COLORS: dict[str, colors.Color] = {
    "pathogenic":             colors.HexColor("#dc2626"),
    "likely pathogenic":      colors.HexColor("#ea580c"),
    "uncertain significance": colors.HexColor("#ca8a04"),
    "likely benign":          colors.HexColor("#64748b"),
    "benign":                 colors.HexColor("#16a34a"),
}


def _sig_color(sig: str) -> colors.Color:
    s = sig.lower()
    for key, col in _SIG_COLORS.items():
        if key in s:
            return col
    return colors.HexColor("#9ca3af")


def _sig_counts(variants: list[dict[str, Any]]) -> tuple[dict[str, int], int]:
    """Return (classification_buckets, hotspot_count) in a single pass."""
    buckets = {
        "Pathogenic": 0,
        "Likely pathogenic": 0,
        "VUS": 0,
        "Likely benign": 0,
        "Benign": 0,
        "Unknown": 0,
    }
    hotspots = 0
    for v in variants:
        sig = v.get("significance", "").lower()
        if "likely pathogenic" in sig:
            buckets["Likely pathogenic"] += 1
        elif "pathogenic" in sig:
            buckets["Pathogenic"] += 1
        elif "likely benign" in sig:
            buckets["Likely benign"] += 1
        elif "benign" in sig:
            buckets["Benign"] += 1
        elif "uncertain" in sig or "vus" in sig:
            buckets["VUS"] += 1
        else:
            buckets["Unknown"] += 1
        if v.get("hotspot"):
            hotspots += 1
    return buckets, hotspots


def _make_bar_chart(counts: dict[str, int]) -> Drawing:
    labels = list(counts.keys())
    values = list(counts.values())

    d = Drawing(380, 140)

    chart = VerticalBarChart()
    chart.x = 40
    chart.y = 20
    chart.width = 320
    chart.height = 100
    chart.data = [values]
    chart.strokeColor = colors.HexColor("#e5e7eb")
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max(values) + 1 if values else 5
    chart.valueAxis.valueStep = max(1, (max(values) + 1) // 5) if values else 1
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 7
    chart.categoryAxis.labels.angle = 15
    chart.categoryAxis.labels.dy = -6

    bar_colors = list(_SIG_COLORS.values()) + [colors.HexColor("#9ca3af")]
    for i, col in enumerate(bar_colors):
        chart.bars[0, i].fillColor = col

    d.add(chart)
    return d


# ── Shared table style helpers ─────────────────────────────────────────────────

_HEADER_BG   = colors.HexColor("#1e3a5f")
_HEADER_BG2  = colors.HexColor("#374151")
_STRIPE      = colors.HexColor("#f9fafb")
_GRID_COLOR  = colors.HexColor("#e5e7eb")

_BASE_TABLE_STYLE = [
    ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
    ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
    ("FONTSIZE",      (0, 0), (-1, -1), 6),
    ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
    ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, _STRIPE]),
    ("GRID",          (0, 0), (-1, -1), 0.3, _GRID_COLOR),
    ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
    ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING",    (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ("WORDWRAP",      (0, 0), (-1, -1), True),
]


def _fmt(val: Any, decimals: int = 4) -> str:
    return f"{val:.{decimals}f}" if isinstance(val, (int, float)) else "—"


def _fmt_score_pred(score: Any, pred: str, decimals: int = 3) -> str:
    """Format a (score, prediction) pair for table cells."""
    if score is not None:
        return f"{_fmt(score, decimals)}\n({pred})" if pred else _fmt(score, decimals)
    return pred or "—"


def _table_style(*extras: tuple) -> TableStyle:
    """Build a TableStyle from _BASE_TABLE_STYLE plus caller-supplied overrides."""
    return TableStyle(list(_BASE_TABLE_STYLE) + list(extras))


def generate_pdf(
    job_id: str,
    variants: list[dict[str, Any]],
    output_path: str,
) -> tuple[str, str]:
    """Write the mutation assessment PDF to output_path.

    Returns (output_path, sha256_hex) where sha256_hex is the hex digest of
    the PDF bytes, suitable for tamper-detection / clinical audit.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title2", parent=styles["Title"], fontSize=18, spaceAfter=4,
        textColor=colors.HexColor("#111827"),
    )
    h2_style = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=11, spaceBefore=12, spaceAfter=4,
        textColor=colors.HexColor("#374151"),
    )
    normal = ParagraphStyle(
        "Normal2", parent=styles["Normal"], fontSize=9,
        textColor=colors.HexColor("#374151"),
    )
    disclaimer_style = ParagraphStyle(
        "Disclaimer", parent=styles["Normal"], fontSize=8,
        textColor=colors.HexColor("#9ca3af"), spaceAfter=0,
    )

    # Landscape A4: 841.9 x 595.3 pt  (297mm x 210mm)
    page_w, page_h = landscape(A4)
    left_margin = right_margin = top_margin = bottom_margin = 2 * cm
    usable_w = page_w - left_margin - right_margin   # ~761 pt

    doc = SimpleDocTemplate(
        output_path,
        pagesize=landscape(A4),
        leftMargin=left_margin,
        rightMargin=right_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
    )

    story: list[Any] = []

    # ── Header ─────────────────────────────────────────────────────────────
    story.append(Paragraph("Mutation Assessment Report", title_style))
    story.append(Paragraph(
        f"Job ID: <font name='Courier' size='8'>{job_id}</font> &nbsp;·&nbsp; "
        f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        normal,
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=_GRID_COLOR, spaceAfter=8))

    # ── Summary stats ──────────────────────────────────────────────────────
    counts, hotspots = _sig_counts(variants)
    total      = len(variants)
    pathogenic = counts["Pathogenic"] + counts["Likely pathogenic"]

    summary_data = [
        ["Total Variants", "Pathogenic / LP", "Cancer Hotspots"],
        [str(total), str(pathogenic), str(hotspots)],
    ]
    summary_table = Table(summary_data, colWidths=[5.5 * cm, 5.5 * cm, 5.5 * cm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 9),
        ("FONTNAME",      (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 1), (-1, 1), 18),
        ("TEXTCOLOR",     (0, 1), (0, 1), colors.HexColor("#111827")),
        ("TEXTCOLOR",     (1, 1), (1, 1), colors.HexColor("#dc2626")),
        ("TEXTCOLOR",     (2, 1), (2, 1), colors.HexColor("#d97706")),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.HexColor("#f9fafb"), colors.white]),
        ("BOX",           (0, 0), (-1, -1), 0.5, _GRID_COLOR),
        ("INNERGRID",     (0, 0), (-1, -1), 0.3, _GRID_COLOR),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 8))

    # ── Classification bar chart ───────────────────────────────────────────
    if total > 0:
        story.append(Paragraph("Variant Classification", h2_style))
        story.append(_make_bar_chart(counts))
        story.append(Spacer(1, 8))

    # ══════════════════════════════════════════════════════════════════════
    # Table A — Clinical Summary
    # Columns: Chr | Pos | Ref | Alt | Gene | ClinVar Sig | InterVar/ACMG |
    #          gnomAD AF | AF popmax | Hotspot | rsID
    # ══════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Table A — Clinical Summary", h2_style))

    ta_headers = [
        "Chr", "Pos", "Ref", "Alt", "Gene",
        "ClinVar Sig.", "InterVar/ACMG",
        "gnomAD AF", "AF popmax",
        "Hotspot", "rsID",
    ]
    ta_data = [ta_headers]

    for v in variants:
        af_raw    = v.get("af")
        af_pm_raw = v.get("af_popmax")

        acmg_criteria = v.get("acmg_criteria") or []
        acmg_str = ", ".join(acmg_criteria[:4]) if acmg_criteria else ""
        intervar_str = v.get("intervar_class", "") or ""
        intervar_cell = f"{intervar_str}\n{acmg_str}" if acmg_str else intervar_str or "—"

        ta_data.append([
            str(v.get("chrom", "")),
            str(v.get("pos", "")),
            str(v.get("ref", "")),
            str(v.get("alt", "")),
            str(v.get("gene", "—")),
            str(v.get("significance", "Unknown")),
            intervar_cell,
            _fmt(af_raw, 5),
            _fmt(af_pm_raw, 5),
            "Yes" if v.get("hotspot") else "—",
            str(v.get("rsid", ".")),
        ])

    # Total usable width for portrait-ish columns; distribute proportionally
    ta_col_w = [
        1.1*cm, 1.7*cm, 0.9*cm, 0.9*cm, 1.6*cm,
        3.0*cm, 3.2*cm,
        1.6*cm, 1.6*cm,
        1.2*cm, 2.0*cm,
    ]
    ta_table = Table(ta_data, colWidths=ta_col_w, repeatRows=1)

    ta_extra: list[tuple] = [
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("ALIGN",      (4, 1), (6, -1), "LEFT"),
    ]
    for row_i, v in enumerate(variants, start=1):
        sig = v.get("significance", "").lower()
        if "pathogenic" in sig:
            bg = colors.HexColor("#fff1f2") if "likely" in sig else colors.HexColor("#fee2e2")
            ta_extra.append(("BACKGROUND", (0, row_i), (-1, row_i), bg))
        ta_extra.append(("TEXTCOLOR", (5, row_i), (5, row_i), _sig_color(v.get("significance", ""))))
        ta_extra.append(("FONTNAME",  (5, row_i), (5, row_i), "Helvetica-Bold"))

    ta_table.setStyle(_table_style(*ta_extra))
    story.append(ta_table)
    story.append(Spacer(1, 12))

    # ══════════════════════════════════════════════════════════════════════
    # Table B — Computational Scores  (new page, full landscape width)
    # Columns: Gene | Consequence | SIFT | PolyPhen | CADD Phred |
    #          REVEL | MetaLR | MetaSVM | MutationTaster | SpliceAI Δ |
    #          GERP++ | PhyloP
    # ══════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("Table B — Computational Scores", h2_style))

    tb_headers = [
        "Gene", "Consequence",
        "SIFT", "PolyPhen",
        "CADD\nPhred",
        "REVEL", "MetaLR", "MetaSVM",
        "Mutation\nTaster",
        "SpliceAI\nΔmax",
        "GERP++", "PhyloP",
    ]
    tb_data = [tb_headers]

    for v in variants:
        sift_str = _fmt_score_pred(v.get("sift_score"),     v.get("sift_pred", "") or "")
        pp_str   = _fmt_score_pred(v.get("polyphen_score"), v.get("polyphen_pred", "") or "")
        mt_pred  = v.get("mutation_taster_pred", "") or ""
        mt_score = v.get("mutation_taster_score")
        mt_str   = _fmt_score_pred(mt_score, mt_pred)

        tb_data.append([
            str(v.get("gene", "—")),
            str(v.get("consequence", "—") or "—"),
            sift_str,
            pp_str,
            _fmt(v.get("cadd_phred"), 1),
            _fmt(v.get("revel"), 3),
            _fmt(v.get("metalr"), 3),
            _fmt(v.get("metasvm"), 3),
            mt_str,
            _fmt(v.get("spliceai_ds_max"), 3),
            _fmt(v.get("gerp_rs"), 2),
            _fmt(v.get("phylop"), 2),
        ])

    # Distribute usable_w across 12 columns
    tb_col_w = [
        1.6*cm,  # Gene
        2.8*cm,  # Consequence
        2.0*cm,  # SIFT
        2.2*cm,  # PolyPhen
        1.3*cm,  # CADD
        1.3*cm,  # REVEL
        1.3*cm,  # MetaLR
        1.3*cm,  # MetaSVM
        2.0*cm,  # MutationTaster
        1.4*cm,  # SpliceAI
        1.3*cm,  # GERP++
        1.3*cm,  # PhyloP
    ]
    tb_table = Table(tb_data, colWidths=tb_col_w, repeatRows=1)

    tb_table.setStyle(_table_style(
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("ALIGN",      (0, 1), (1, -1), "LEFT"),
    ))
    story.append(tb_table)
    story.append(Spacer(1, 12))

    # ══════════════════════════════════════════════════════════════════════
    # Table C — Gene Disease Associations  (new page, full landscape width)
    # Columns: Gene | Protein (UniProt) | OMIM Disease | ClinGen Validity |
    #          GenCC | Orphanet | HPO Terms (top 3) | LOVD Variants
    # ══════════════════════════════════════════════════════════════════════
    seen_genes_c: set[str] = set()
    tc_rows: list[list[str]] = []
    for v in variants:
        gene = v.get("gene", "")
        if not gene or gene in seen_genes_c:
            continue
        seen_genes_c.add(gene)

        protein_name = v.get("protein_name", "") or ""

        omim_disease = v.get("disease", "") or ""
        omim_id      = v.get("omim_id", "") or ""
        omim_cell    = f"{omim_disease} [{omim_id}]" if omim_disease and omim_id else omim_disease or omim_id or "—"

        clingen_items = v.get("clingen_classifications") or []
        clingen_str = "; ".join(
            f"{c.get('classification', '')} ({c.get('disease', '')})"
            for c in clingen_items[:2]
        ) or "—"

        gencc_items = v.get("gencc_diseases") or []
        gencc_str = "; ".join(
            f"{g.get('classification', '')} — {g.get('disease', '')}"
            for g in gencc_items[:2]
        ) or "—"

        orpha_items = v.get("orpha_diseases") or []
        orpha_str = "; ".join(d.get("name", "") for d in orpha_items[:2]) or "—"

        hpo_items = v.get("hpo_terms") or []
        hpo_str = "; ".join(h.get("term", "") for h in hpo_items[:3]) or "—"

        lovd_count = v.get("lovd_variant_count")
        lovd_str = str(lovd_count) if lovd_count is not None else "—"

        tc_rows.append([
            gene,
            protein_name[:60] or "—",
            omim_cell,
            clingen_str,
            gencc_str,
            orpha_str,
            hpo_str,
            lovd_str,
        ])

    if tc_rows:
        story.append(PageBreak())
        story.append(Paragraph("Table C — Gene Disease Associations", h2_style))

        tc_headers = [
            "Gene", "Protein (UniProt)",
            "OMIM Disease", "ClinGen Validity",
            "GenCC", "Orphanet",
            "HPO Terms (top 3)", "LOVD\nVariants",
        ]
        tc_data = [tc_headers] + tc_rows
        tc_col_w = [
            1.5*cm,   # Gene
            3.5*cm,   # Protein
            3.5*cm,   # OMIM
            3.5*cm,   # ClinGen
            3.5*cm,   # GenCC
            3.0*cm,   # Orphanet
            4.5*cm,   # HPO
            1.5*cm,   # LOVD
        ]
        tc_table = Table(tc_data, colWidths=tc_col_w, repeatRows=1)

        tc_table.setStyle(_table_style(
            ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG2),
            ("ALIGN",      (0, 0), (-1, -1), "LEFT"),
            ("VALIGN",     (0, 0), (-1, -1), "TOP"),
        ))
        story.append(tc_table)
        story.append(Spacer(1, 12))

    # ── Data Sources ────────────────────────────────────────────────────────
    story.append(Paragraph("Data Sources", h2_style))
    story.append(Paragraph(
        "<b>ClinVar (NCBI)</b> — clinical significance and pathogenicity classifications. "
        "<b>gnomAD v4.1</b> — population allele frequencies and popmax AF. "
        "<b>Ensembl VEP</b> — variant consequence, SIFT and PolyPhen-2 scores, transcript annotation. "
        "<b>CADD v1.7</b> — combined annotation-dependent depletion phred score. "
        "<b>MyVariant.info</b> — REVEL, MetaLR, MetaSVM, MutationTaster, GERP++, PhyloP (dbNSFP). "
        "<b>SpliceAI (Broad Institute)</b> — splice site disruption delta scores. "
        "<b>InterVar (WinterVar)</b> — automated ACMG/AMP 2015 variant classification. "
        "<b>CancerHotspots.org</b> — recurrent cancer driver mutation hotspots. "
        "<b>dbSNP (NCBI)</b> — population variant rsID identifiers. "
        "<b>UniProt</b> — protein name and function. "
        "<b>HGNC</b> — authoritative gene symbol, locus group, Ensembl/Entrez IDs. "
        "<b>ClinGen Evidence Repository</b> — gene-disease validity classifications. "
        "<b>GenCC</b> — aggregated gene-disease validity (ClinGen, OMIM, Orphanet, PanelApp). "
        "<b>HPO / Ensembl</b> — phenotype terms associated with gene. "
        "<b>LOVD</b> — Leiden Open Variation Database locus-specific variant count. "
        "<b>OMIM</b> — gene-disease relationships and inheritance (if API key configured). "
        "<b>Orphanet</b> — rare disease gene associations (if API key configured).",
        normal,
    ))
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_GRID_COLOR, spaceAfter=6))

    # ── Disclaimer ─────────────────────────────────────────────────────────
    story.append(Paragraph(
        "⚠ RESEARCH USE ONLY — This report is generated automatically from public databases "
        "and is NOT intended for clinical diagnosis, treatment, or patient care decisions. "
        "Variant interpretations may be incomplete or inaccurate. Always consult a certified "
        "clinical geneticist or medical professional for clinical variant interpretation.",
        disclaimer_style,
    ))

    doc.build(story)

    # Compute SHA-256 of the written PDF for tamper-detection / clinical signing
    with open(output_path, "rb") as fh:
        pdf_hash = hashlib.sha256(fh.read()).hexdigest()

    return output_path, pdf_hash
