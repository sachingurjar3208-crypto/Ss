"""Cricket-playing nations available for the `countryflag:` field.

CRICKET_COUNTRIES is an ordered list of (display_name, flag_emoji) tuples.
resolve_country() does a forgiving, case-insensitive lookup so the owner can
type "india", "IND", or "India" and still hit the right entry.
"""

from __future__ import annotations

CRICKET_COUNTRIES: list[tuple[str, str]] = [
    ("India", "🇮🇳"),
    ("Pakistan", "🇵🇰"),
    ("Australia", "🇦🇺"),
    ("England", "🏴"),
    ("South Africa", "🇿🇦"),
    ("New Zealand", "🇳🇿"),
    ("Sri Lanka", "🇱🇰"),
    ("Bangladesh", "🇧🇩"),
    ("Afghanistan", "🇦🇫"),
    ("West Indies", "🏝️"),
    ("Zimbabwe", "🇿🇼"),
    ("Ireland", "🇮🇪"),
    ("Scotland", "🏴"),
    ("Netherlands", "🇳🇱"),
    ("UAE", "🇦🇪"),
    ("Nepal", "🇳🇵"),
    ("Namibia", "🇳🇦"),
    ("Oman", "🇴🇲"),
    ("USA", "🇺🇸"),
    ("Canada", "🇨🇦"),
    ("Kenya", "🇰🇪"),
    ("Papua New Guinea", "🇵🇬"),
    ("Hong Kong", "🇭🇰"),
    ("Singapore", "🇸🇬"),
    ("Malaysia", "🇲🇾"),
    ("Bermuda", "🇧🇲"),
    ("Jersey", "🇯🇪"),
    ("Guernsey", "🇬🇬"),
    ("Italy", "🇮🇹"),
    ("Germany", "🇩🇪"),
    ("Denmark", "🇩🇰"),
    ("Norway", "🇳🇴"),
    ("Qatar", "🇶🇦"),
    ("Kuwait", "🇰🇼"),
    ("Saudi Arabia", "🇸🇦"),
    ("Bahrain", "🇧🇭"),
    ("Botswana", "🇧🇼"),
    ("Uganda", "🇺🇬"),
    ("Nigeria", "🇳🇬"),
    ("Rwanda", "🇷🇼"),
    ("Vanuatu", "🇻🇺"),
    ("Fiji", "🇫🇯"),
    ("Thailand", "🇹🇭"),
    ("China", "🇨🇳"),
    ("Malawi", "🇲🇼"),
]

_LOOKUP = {name.lower(): (name, emoji) for name, emoji in CRICKET_COUNTRIES}
_ALIASES = {
    "ind": "india", "pak": "pakistan", "aus": "australia", "eng": "england",
    "sa": "south africa", "rsa": "south africa", "nz": "new zealand",
    "sl": "sri lanka", "ban": "bangladesh", "afg": "afghanistan",
    "wi": "west indies", "zim": "zimbabwe", "ire": "ireland",
    "sco": "scotland", "ned": "netherlands", "uae": "uae", "nep": "nepal",
    "nam": "namibia", "usa": "usa", "can": "canada", "ken": "kenya",
    "png": "papua new guinea", "hk": "hong kong", "sin": "singapore",
    "mal": "malaysia", "ber": "bermuda",
}


def resolve_country(query: str) -> tuple[str, str] | None:
    """Return (name, emoji) for a query, or None if nothing matches."""
    if not query:
        return None
    q = query.strip().lower()
    if q in _LOOKUP:
        return _LOOKUP[q]
    if q in _ALIASES and _ALIASES[q] in _LOOKUP:
        return _LOOKUP[_ALIASES[q]]
    # Fall back to "starts with" / "contains" matching
    for name_lower, value in _LOOKUP.items():
        if name_lower.startswith(q) or q in name_lower:
            return value
    return None


def country_list_text() -> str:
    """Formatted list of all supported countries, for error messages."""
    return ", ".join(name for name, _ in CRICKET_COUNTRIES)
