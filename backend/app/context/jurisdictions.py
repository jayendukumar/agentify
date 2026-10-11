"""Epic 27, US27.1/US27.3: a small built-in jurisdiction table so the
profile wizard can turn country codes into named jurisdictions with the
right parent ("DE" -> parent "EU") without the user typing either.

Deliberately not exhaustive: an unknown code still becomes a jurisdiction
(named after its code, no parent), and a user can rename or re-parent it.
"""

from __future__ import annotations

EU_MEMBERS = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France",
    "DE": "Germany", "GR": "Greece", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
    "LV": "Latvia", "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "SK": "Slovakia", "SI": "Slovenia",
    "ES": "Spain", "SE": "Sweden",
}

OTHER_COUNTRIES = {
    "EU": "European Union", "UK": "United Kingdom", "GB": "United Kingdom", "US": "United States",
    "CA": "Canada", "AU": "Australia", "NZ": "New Zealand", "SG": "Singapore", "IN": "India",
    "JP": "Japan", "CH": "Switzerland", "NO": "Norway", "AE": "United Arab Emirates",
    "HK": "Hong Kong", "MY": "Malaysia", "ZA": "South Africa", "BR": "Brazil", "MX": "Mexico",
}


def normalize_code(code: str) -> str:
    code = code.strip().upper()
    return "UK" if code == "GB" else code


def builtin_name(code: str) -> str:
    code = normalize_code(code)
    if code in EU_MEMBERS:
        return EU_MEMBERS[code]
    if code in OTHER_COUNTRIES:
        return OTHER_COUNTRIES[code]
    if code.startswith("US-") and len(code) > 3:
        return f"United States -- {code[3:]}"
    return code


def builtin_parent(code: str) -> str | None:
    code = normalize_code(code)
    if code in EU_MEMBERS:
        return "EU"
    if "-" in code:
        return code.split("-", 1)[0]
    return None
