"""TC Kimlik No (Turkish National Identity Number) validation.

The TCKN is an 11-digit number. The last two digits are check digits
computed from the first nine via the official algorithm published by
the Turkish Ministry of Interior (Nüfus ve Vatandaşlık İşleri).

Algorithm:
  d10 = (7*(d1+d2+d3+d4+d5) - (d6+d7+d8+d9)) mod 10
  d11 = (d1+d2+..+d10) mod 10

Reference: https://tckimlik.nvi.gov.tr/
"""


def validate_tckn(tc: str) -> bool:
    """Return True if *tc* is a structurally valid Turkish national ID number.

    Does NOT verify the number against a government database — only validates
    the checksum and format.
    """
    if not tc or len(tc) != 11:
        return False
    if not tc.isdigit():
        return False
    if tc[0] == "0":
        return False
    d = [int(c) for c in tc]
    expected_d10 = (7 * sum(d[:5]) - sum(d[5:9])) % 10
    expected_d11 = sum(d[:10]) % 10
    return d[9] == expected_d10 and d[10] == expected_d11


def format_tckn(tc: str) -> str:
    """Strip spaces/dashes and return normalised 11-digit string, or raise ValueError."""
    cleaned = tc.replace(" ", "").replace("-", "")
    if not validate_tckn(cleaned):
        raise ValueError(f"Invalid TC Kimlik No: {tc!r}")
    return cleaned
