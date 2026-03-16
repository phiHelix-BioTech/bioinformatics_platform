"""Lightweight VCF input validation.

Checks the first few lines of an uploaded file for mandatory VCF headers
before submitting it to a pipeline.  Avoids wasting compute on corrupt or
wrong-format uploads.

Supported: VCF 4.x (plain text or gzip-compressed).
"""
from __future__ import annotations

import gzip
import io


class VCFValidationError(ValueError):
    pass


def validate_vcf_bytes(data: bytes, max_header_bytes: int = 4096) -> None:
    """Validate the header of a VCF file given its raw bytes (or first chunk).

    Raises VCFValidationError with a human-readable message on failure.
    Silently returns if the file looks valid.

    ``data`` should be at least the first 4 KB of the file.
    For large files, pass only the beginning — we only need the header.
    """
    # Decompress if gzipped
    if data[:2] == b"\x1f\x8b":
        try:
            with gzip.open(io.BytesIO(data), "rb") as fh:
                data = fh.read(max_header_bytes)
        except Exception as exc:
            raise VCFValidationError(f"Cannot decompress gzip VCF: {exc}") from exc

    try:
        text = data[:max_header_bytes].decode("utf-8", errors="replace")
    except Exception as exc:
        raise VCFValidationError(f"Cannot decode file as text: {exc}") from exc

    lines = text.splitlines()
    if not lines:
        raise VCFValidationError("File appears to be empty.")

    # Must start with ##fileformat=VCF
    if not lines[0].startswith("##fileformat=VCF"):
        raise VCFValidationError(
            "File does not appear to be a valid VCF: "
            f"first line must start with '##fileformat=VCF', got: {lines[0][:80]!r}"
        )

    # Must have a #CHROM header line somewhere in the first 200 lines
    has_chrom = any(l.startswith("#CHROM") for l in lines[:200])
    if not has_chrom:
        raise VCFValidationError(
            "VCF file is missing the mandatory #CHROM column header line."
        )
