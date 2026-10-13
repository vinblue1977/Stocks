"""Areas of a portfolio (US core, international, ...) and which area each known fund or stock belongs to.

The user sets a target share per area; the Checkup compares the portfolio's mix against it.
Tickers not listed here are "unassigned" until the user picks an area for them.
"""

from __future__ import annotations

from collections.abc import Mapping

# Display order; key -> label
AREAS: dict[str, str] = {
    "us": "US core",
    "intl": "International",
    "dividend": "Dividend / quality",
    "small": "Small / mid companies",
    "growth": "Growth tilt (Nasdaq)",
    "sector": "Single sector",
    "bonds": "Bonds",
}
UNASSIGNED = "unassigned"

KNOWN: dict[str, str] = {
    **dict.fromkeys(["VTI", "ITOT", "SCHB", "VOO", "SPY", "IVV", "IWB", "SCHX", "SPLG", "VV"], "us"),
    **dict.fromkeys(["VXUS", "IXUS", "VEA", "IEFA", "SCHF", "VWO", "IEMG", "SCHE", "VEU", "SPDW"], "intl"),
    **dict.fromkeys(["SCHD", "DGRO", "DVY", "SPYD", "VIG", "VYM", "HDV", "NOBL", "VTV", "MGV", "IVE", "SCHV"], "dividend"),
    **dict.fromkeys(["AVUV", "VB", "VO", "IJH", "IJR", "IWM", "SCHA", "SCHM", "VBR", "VOE"], "small"),
    **dict.fromkeys(["QQQ", "QQQM", "ONEQ", "VUG", "SCHG", "IWF", "MGK"], "growth"),
    **dict.fromkeys(["SMH", "SOXX", "NVDA", "AVGO", "AMD", "TSM", "XLK", "VGT", "XLE", "XLV", "XLF"], "sector"),
    **dict.fromkeys(["BND", "AGG", "VTEB", "BNDX", "SCHZ", "VGIT", "TLT", "SGOV", "BIL"], "bonds"),
}

# Shortlist shown in the target editor: (first choice, alternatives).
SHORTLIST: dict[str, str] = {
    "us": "VTI · ITOT · SCHB (0.03%)",
    "intl": "VXUS (0.05%) · IXUS · VEA + VWO",
    "dividend": "SCHD (0.06%) · DGRO · VIG",
    "small": "AVUV (0.25%) · VB · IJH (0.05%)",
    "growth": "QQQM (0.15%) · ONEQ",
    "sector": "SMH · SOXX (0.35%)",
    "bonds": "BND · AGG (0.03%) · VTEB",
}


def area_of(ticker: str, overrides: Mapping[str, str] | None = None) -> str:
    """The user's choice first, then the built-in map; otherwise unassigned."""
    if overrides and overrides.get(ticker) in AREAS:
        return overrides[ticker]
    return KNOWN.get(ticker, UNASSIGNED)
