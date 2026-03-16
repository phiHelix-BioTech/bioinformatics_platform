"""Structural variant / CNV parser.

Reads a VCF file and extracts SV/CNV records based on the SVTYPE INFO field.
Supports the standard SV types: DEL, DUP, INV, INS, BND, CNV.

Returns a list of dicts suitable for JSON serialisation and frontend display.
"""
from __future__ import annotations

import gzip
import re
from pathlib import Path
from typing import IO


_SV_TYPES = {"DEL", "DUP", "INV", "INS", "BND", "CNV", "TRA"}

_INFO_RE = re.compile(r"([A-Z_]+)=([^;]+)")


def _open(path: str) -> IO[str]:
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rt")
    return open(p, "r")


def _parse_info(raw: str) -> dict:
    return {m.group(1): m.group(2) for m in _INFO_RE.finditer(raw)}


def parse_sv_vcf(path: str, max_records: int = 5_000) -> list[dict]:
    """Parse *path* (VCF or VCF.gz) and return SV/CNV records.

    Each record dict contains:
        chrom, pos, end, svtype, svlen, qual, filter, gene, af, info_raw
    """
    records: list[dict] = []
    try:
        with _open(path) as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 8:
                    continue
                chrom, pos_s, _, ref, alt, qual, flt, info_s = parts[:8]
                info = _parse_info(info_s)
                svtype = info.get("SVTYPE", "").upper()
                if svtype not in _SV_TYPES:
                    # Also catch ALT allele tags like <DEL>, <DUP:TANDEM>
                    for a in alt.split(","):
                        a = a.strip("<>").split(":")[0].upper()
                        if a in _SV_TYPES:
                            svtype = a
                            break
                if svtype not in _SV_TYPES:
                    continue

                try:
                    pos = int(pos_s)
                except ValueError:
                    pos = 0

                end_s = info.get("END")
                try:
                    end = int(end_s) if end_s else None
                except ValueError:
                    end = None

                svlen_s = info.get("SVLEN")
                try:
                    svlen = abs(int(svlen_s)) if svlen_s else None
                except ValueError:
                    svlen = None

                af_s = info.get("AF") or info.get("MAF")
                try:
                    af = float(af_s.split(",")[0]) if af_s else None
                except ValueError:
                    af = None

                records.append({
                    "chrom": chrom,
                    "pos": pos,
                    "end": end,
                    "svtype": svtype,
                    "svlen": svlen,
                    "qual": qual if qual != "." else None,
                    "filter": flt,
                    "gene": info.get("GENE") or info.get("ANN", "").split("|")[3] if "ANN" in info else None,
                    "af": af,
                    "info_raw": info_s[:500],  # cap to keep payload small
                })
                if len(records) >= max_records:
                    break
    except Exception:
        pass  # return what we have so far

    return records
