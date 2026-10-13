"""The monthly checkup as a printable PDF (three pages): summary and mix, next dollars and history, risk and holdings.

Built with ReportLab from the same numbers as the Checkup page (`checkup.build`). Light colors so it prints well.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

from portfolio import checkup
from portfolio.buckets import AREAS

INK = colors.HexColor("#1B2433")
MUTED = colors.HexColor("#5F6B80")
RULE = colors.HexColor("#DCE2EA")
BLUE = colors.HexColor("#2F80C8")
ORANGE = colors.HexColor("#D9822B")
NAVY = colors.HexColor("#0B1220")
SOFT = colors.HexColor("#EEF3F8")
MARKET_GREY = colors.HexColor("#9AA6B8")
WIDTH = 6.9 * inch  # letter width minus 0.6" margins


def _pct(v: float | None, digits: int = 0) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _spct(v: float | None, digits: int = 0) -> str:
    return "—" if v is None else f"{v * 100:+.{digits}f}%"  # ASCII minus: shows in every PDF viewer


def _usd(v: float) -> str:
    return f"${abs(v):,.0f}"


def _style(size: float, leading: float, color: Any = INK, bold: bool = False, **kw: Any) -> ParagraphStyle:
    return ParagraphStyle(
        "s", fontName="Helvetica-Bold" if bold else "Helvetica", fontSize=size, leading=leading, textColor=color, **kw
    )


H1 = _style(20, 24, bold=True)
H2 = _style(13, 17, bold=True, spaceBefore=6, spaceAfter=6)
SMALL = _style(8.5, 11, MUTED)
BULLET = _style(10.5, 15, leftIndent=12, bulletIndent=0)


def _tiles(home: Mapping[str, Any]) -> Table:
    r = home["risk"]
    m = r.get("market") or {}
    cells = [
        ("Household value", _usd(home["total_value"]), "Across both portfolios"),
        ("Last 12 months", _spct(r["return_1y"]), f"Market {_spct(m.get('return_1y'))}"),
        (
            "Swings vs. market",
            "—" if r["beta"] is None else f"{r['beta']:.1f}×",
            f"Volatility {_pct(r['volatility'])} vs {_pct(m.get('volatility'))}",
        ),
        ("Spread", f"~{round(home['effective_holdings'] or 0)} holdings", f"{home['holdings_count']} tickers, by size"),
    ]
    label, big, sub = _style(8, 11, MUTED), _style(17, 22, bold=True), _style(8, 10, MUTED)
    table = Table([[[Paragraph(a, label), Paragraph(b, big), Paragraph(c, sub)] for a, b, c in cells]], colWidths=[WIDTH / 4] * 4)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("INNERGRID", (0, 0), (-1, -1), 4, colors.white),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _mix_chart(view: Mapping[str, Any], targets: Mapping[str, Any]) -> Drawing:
    rows = [d for d in view["drift"] if d["now"] or d["target"]]
    row_h, label_w, nums_w = 30, 1.7 * inch, 1.55 * inch
    bar_w = WIDTH - label_w - nums_w
    ceiling = targets.get("sector_ceiling", 100) / 100
    drawing = Drawing(WIDTH, row_h * len(rows) + 6)
    for i, d in enumerate(rows):
        y = drawing.height - (i + 1) * row_h
        over = (d["area"] == "sector" and d["now"] > ceiling) or d["now"] - d["target"] > 0.05
        fund = (targets["areas"].get(d["area"]) or {}).get("fund") or "—"
        drawing.add(String(0, y + 11, d["label"], fontName="Helvetica-Bold", fontSize=9.5, fillColor=INK))
        drawing.add(String(0, y + 1, f"Your fund: {fund}", fontName="Helvetica", fontSize=7.5, fillColor=MUTED))
        drawing.add(Rect(label_w, y + 6, bar_w, 10, rx=5, ry=5, fillColor=SOFT, strokeColor=None))
        fill_w = bar_w * min(1.0, d["now"])
        if fill_w >= 1:  # nothing drawn for an empty area
            radius = min(5, fill_w / 2)
            drawing.add(
                Rect(label_w, y + 6, fill_w, 10, rx=radius, ry=radius, fillColor=ORANGE if over else BLUE, strokeColor=None)
            )
        target_x = label_w + bar_w * d["target"]
        drawing.add(Line(target_x, y + 2, target_x, y + 20, strokeColor=INK, strokeWidth=1.6))
        drawing.add(
            String(
                WIDTH,
                y + 11,
                f"{_pct(d['now'])}  /  {_pct(d['target'])}",
                fontName="Helvetica",
                fontSize=9,
                fillColor=INK,
                textAnchor="end",
            )
        )
        near = abs(d["now"] - d["target"]) < 0.02
        note = "on target" if near else f"{'over' if d['gap'] > 0 else 'under'} by {_usd(d['gap'])}"
        tone = MUTED if near else ORANGE if d["gap"] > 0 else BLUE
        drawing.add(String(WIDTH, y + 1, note, fontName="Helvetica", fontSize=7.5, fillColor=tone, textAnchor="end"))
    return drawing


def _history_chart(points: Sequence[Mapping[str, Any]], height: float = 2.4 * inch) -> Drawing | None:
    """Household and market (VTI), both rebased to 0% at the start of the year."""
    points = [p for p in points if p.get("market")]
    if len(points) < 2:
        return None
    mine = [p["value"] / points[0]["value"] for p in points]
    market = [p["market"] / points[0]["market"] for p in points]
    lo, hi = min(mine + market), max(mine + market)
    pad = (hi - lo) * 0.08 or 0.01
    lo, hi = lo - pad, hi + pad
    left, bottom, top = 40, 22, height - 10

    def x(i: int) -> float:
        return left + i / (len(points) - 1) * (WIDTH - left - 6)

    def y(v: float) -> float:
        return bottom + (v - lo) / (hi - lo) * (top - bottom)

    drawing = Drawing(WIDTH, height)
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        drawing.add(Line(left, y(v), WIDTH - 6, y(v), strokeColor=RULE, strokeWidth=0.6))
        drawing.add(String(left - 4, y(v) - 3, _spct(v - 1), fontName="Helvetica", fontSize=7, fillColor=MUTED, textAnchor="end"))
    for i in range(0, len(points), 63):
        label = date.fromisoformat(points[i]["date"]).strftime("%b %Y")
        drawing.add(
            String(x(i), 6, label, fontName="Helvetica", fontSize=7, fillColor=MUTED, textAnchor="middle" if i else "start")
        )
    drawing.add(
        PolyLine(
            [c for i, v in enumerate(market) for c in (x(i), y(v))],
            strokeColor=MARKET_GREY,
            strokeWidth=1.3,
            strokeDashArray=[3, 2],
        )
    )
    drawing.add(PolyLine([c for i, v in enumerate(mine) for c in (x(i), y(v))], strokeColor=BLUE, strokeWidth=2))
    last = len(points) - 1
    drawing.add(
        String(
            WIDTH - 6,
            y(mine[-1]) + 6,
            f"Household {_spct(mine[-1] - 1)}",
            fontName="Helvetica-Bold",
            fontSize=8,
            fillColor=BLUE,
            textAnchor="end",
        )
    )
    drawing.add(
        String(
            WIDTH - 6,
            y(market[last]) - 12,
            f"Market (VTI) {_spct(market[-1] - 1)}",
            fontName="Helvetica",
            fontSize=8,
            fillColor=MUTED,
            textAnchor="end",
        )
    )
    return drawing


def _table(rows: list[list[str]], widths: list[float], right_from: int = 1) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1)  # header repeats if a table runs onto the next page
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE),
                ("ALIGN", (right_from, 0), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, MUTED),
            ]
        )
    )
    return table


def build_pdf(
    built: Mapping[str, Any],
    targets: Mapping[str, Any] | None,
    summary: str,
    names: Mapping[str, str],
    month: str,
    split_amount: float = 1000.0,
) -> bytes:
    """`built` is `checkup.build(...)`; `names` maps mine/partner to display names; `month` e.g. "November 2026"."""
    views = built["views"]
    home = views["household"]
    as_of = date.fromisoformat(built["as_of"]).strftime("%b %-d, %Y") if built.get("as_of") else "—"

    def page(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, letter[1] - 0.42 * inch, letter[0], 0.42 * inch, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(0.6 * inch, letter[1] - 0.27 * inch, "Portfolio Tracker · Monthly checkup")
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(letter[0] - 0.6 * inch, letter[1] - 0.27 * inch, month)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(
            0.6 * inch,
            0.4 * inch,
            f"Prices as of {as_of} close. Describes your data and the targets you set; not investment advice.",
        )
        canvas.drawRightString(letter[0] - 0.6 * inch, 0.4 * inch, f"Page {doc.page}")
        canvas.restoreState()

    points = [p.strip() for p in summary.splitlines() if p.strip()]
    story: list[Any] = [
        Paragraph("Your monthly portfolio checkup", H1),
        Spacer(1, 4),
        Paragraph(f"Household · prices as of {as_of} close", SMALL),
        Spacer(1, 12),
        _tiles(home),
        Spacer(1, 16),
        Paragraph("This month", H2),
        *[Paragraph(_escape(p), BULLET, bulletText="•") for p in points],
        Paragraph("Written by Claude on Amazon Bedrock from your numbers and targets.", SMALL),
        Spacer(1, 14),
    ]
    if targets and home["drift"]:
        ceiling = targets.get("sector_ceiling", 100)
        story += [
            Paragraph("Your mix vs. target", H2),
            _mix_chart(home, targets),
            Paragraph(
                "Dark mark = your target. Orange = more than 5 points above target, "
                f"or above your {ceiling}% single-sector ceiling.",
                SMALL,
            ),
        ]
    story.append(PageBreak())

    if targets and home["drift"]:
        shares = {k: v["pct"] / 100 for k, v in targets["areas"].items()}
        parts = checkup.split(home["by_area"], home["total_value"], shares, split_amount)
        rows = [["Area", "Your fund", "Amount"]] + [[AREAS[k], targets["areas"][k].get("fund") or "—", _usd(v)] for k, v in parts]
        story += [
            Paragraph(f"Where the next {_usd(split_amount)} would go", H2),
            Paragraph(
                "Split across the areas furthest below your targets, using the fund you chose for each. No selling.", SMALL
            ),
            Spacer(1, 6),
            _table(rows, [3.2 * inch, 1.6 * inch, 2.1 * inch], right_from=2),
            Spacer(1, 16),
        ]
    people = [p for p in ("mine", "partner") if p in views]
    if len(people) > 1:
        rows = [[""] + [names.get(p, p) for p in people], ["Value"] + [_usd(views[p]["total_value"]) for p in people]]
        for key, label in AREAS.items():
            shares = [views[p]["by_area"].get(key, 0) / views[p]["total_value"] if views[p]["total_value"] else 0 for p in people]
            if any(shares):
                rows.append([label, *[_pct(s) for s in shares]])
        rows.append(["Last 12 months", *[_spct(views[p]["risk"]["return_1y"]) for p in people]])
        rows.append(
            [
                "Swings vs. market",
                *["—" if views[p]["risk"]["beta"] is None else f"{views[p]['risk']['beta']:.1f}×" for p in people],
            ]
        )
        story += [Paragraph("Each of you", H2), _table(rows, [3.2 * inch, 1.85 * inch, 1.85 * inch]), Spacer(1, 16)]
    chart = _history_chart(home.get("history_1y") or [])
    if chart:
        story += [
            Paragraph("Last 12 months vs. the market", H2),
            chart,
            Paragraph("Today's holdings replayed over past prices (how this mix behaved), next to the total US market.", SMALL),
        ]
    story.append(PageBreak())

    r, m = home["risk"], home["risk"].get("market") or {}
    risk_rows = [
        ["", "Household", "Market (VTI)"],
        ["Last 12 months", _spct(r["return_1y"], 1), _spct(m.get("return_1y"), 1)],
        ["Volatility (yearly)", _pct(r["volatility"]), _pct(m.get("volatility"))],
        ["Beta vs. market", "—" if r["beta"] is None else f"{r['beta']:.2f}", "1.00"],
        ["2022 (calendar year)", _spct(r["return_2022"], 1), _spct(m.get("return_2022"), 1)],
        ["Worst drop, 2022", _spct(r["worst_drop_2022"], 1), _spct(m.get("worst_drop_2022"), 1)],
        ["Worst drop, 5 years", _spct(r["worst_drop_5y"], 1), "—"],
    ]
    total = home["total_value"] or 1
    holding_rows = [["Ticker", "Area", "Value", "Share", "12 months"]] + [
        [h["ticker"], AREAS.get(h["area"], "Unassigned"), _usd(h["value"]), _pct(h["value"] / total, 1), _spct(h["return_1y"])]
        for h in home["holdings"]
    ]
    story += [
        Paragraph("Risk vs. the market", H2),
        _table(risk_rows, [3.2 * inch, 1.85 * inch, 1.85 * inch]),
        Spacer(1, 16),
        Paragraph("Holdings", H2),
        _table(holding_rows, [0.9 * inch, 2.3 * inch, 1.3 * inch, 1.1 * inch, 1.3 * inch], right_from=2),
    ]

    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.7 * inch,
        title=f"Monthly portfolio checkup, {month}",
        author="Portfolio Tracker",
    )
    doc.addPageTemplates(
        [PageTemplate(frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")], onPage=page)]
    )
    doc.build(story)
    return buffer.getvalue()


# Characters outside the built-in PDF fonts' set, which some viewers can't show; a lost minus would read as a gain.
_PLAIN = str.maketrans({"\u2212": "-", "\u2248": "~", "\u2192": "->", "\u2265": ">=", "\u2264": "<="})


def _escape(text: str) -> str:
    """Paragraph text is a small markup language; Claude's text must not be read as tags."""
    return text.translate(_PLAIN).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
