#!/usr/bin/env python3
"""Generate SpaceX S-1 second-order winners PDF report."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "reports" / "spacex-ipo-second-order-winners.pdf"
ASSETS = Path(__file__).resolve().parents[2] / "docs" / "reports" / ".assets"


def _save_fig(name: str) -> Path:
    ASSETS.mkdir(parents=True, exist_ok=True)
    path = ASSETS / name
    plt.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close()
    return path


def draw_bucket_tree() -> Path:
    fig, ax = plt.subplots(figsize=(12, 9))
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 9)
    ax.axis("off")
    fig.patch.set_facecolor("#FAFBFC")

    palette = {
        "root": "#1E3A5F",
        "launch": "#2563EB",
        "satellite": "#7C3AED",
        "defense": "#059669",
        "infra": "#D97706",
        "leaf": "#F8FAFC",
        "border": "#CBD5E1",
        "text": "#0F172A",
        "subtext": "#475569",
    }

    def box(x, y, w, h, text, sub="", fc="#FFFFFF", ec=palette["border"], fs=10, bold=False):
        rect = FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.02,rounding_size=0.08",
            linewidth=1.5,
            edgecolor=ec,
            facecolor=fc,
        )
        ax.add_patch(rect)
        weight = "bold" if bold else "normal"
        ax.text(
            x + w / 2,
            y + h / 2 + (0.14 if sub else 0),
            text,
            ha="center",
            va="center",
            fontsize=fs,
            fontweight=weight,
            color=palette["text"],
        )
        if sub:
            ax.text(
                x + w / 2,
                y + h / 2 - 0.24,
                sub,
                ha="center",
                va="center",
                fontsize=7.5,
                color=palette["subtext"],
            )

    def line(x1, y1, x2, y2):
        ax.plot([x1, x2], [y1, y2], color="#94A3B8", linewidth=1.4, zorder=1)

    def arrow(x1, y1, x2, y2):
        ax.add_patch(
            FancyArrowPatch(
                (x1, y1),
                (x2, y2),
                arrowstyle="-|>",
                mutation_scale=11,
                linewidth=1.3,
                color="#94A3B8",
            )
        )

    # Full-width parent spanning all four buckets
    root_x, root_w = 0.4, 11.2
    root_y, root_h = 7.55, 0.8
    box(
        root_x,
        root_y,
        root_w,
        root_h,
        "SpaceX S-1 / IPO",
        "Validates space + AI capex thesis",
        fc="#E0E7FF",
        ec=palette["root"],
        fs=12,
        bold=True,
    )

    # Trunk + horizontal bus — every bucket connects to the full-width parent
    bus_y = 6.55
    line(root_x + root_w / 2, root_y, root_x + root_w / 2, bus_y)
    line(1.5, bus_y, 10.5, bus_y)

    # Staggered sibling buckets (A highest → D lowest)
    buckets = [
        (0.5, 5.55, 2.4, 0.95, "A · Launch &\nSpace Systems", palette["launch"], "RKLB · PL · LUNR", 1.7),
        (3.35, 5.05, 2.4, 0.95, "B · Satellite\nConnectivity", palette["satellite"], "ASTS · IRDM", 4.55),
        (6.2, 5.35, 2.4, 0.95, "C · Defense /\nGov Space", palette["defense"], "LMT · RTX · NOC", 7.4),
        (9.05, 4.75, 2.4, 0.95, "D · AI Infra\nPick-and-Shovel", palette["infra"], "VRT · CEG · NVDA", 10.25),
    ]
    for x, y, w, h, title, color, tickers, bus_x in buckets:
        box(x, y, w, h, title, tickers, fc="#FFFFFF", ec=color, fs=9, bold=True)
        line(bus_x, bus_y, bus_x, y + h)
        arrow(bus_x, y + h, x + w / 2, y + h + 0.02)

    # Staggered child nodes — each bucket's children at descending heights
    leaves = [
        # Bucket A
        (0.35, 4.15, 1.55, 1.0, "RKLB", "Closest public comp\n$602M rev · $2.2B backlog", 1.7),
        (0.35, 2.95, 1.55, 1.0, "PL", "Earth observation\n$307M rev · $900M backlog", 1.7),
        (0.35, 1.75, 1.55, 1.0, "LUNR", "Lunar / NASA\nHigh volatility", 1.7),
        # Bucket B
        (3.55, 3.85, 1.55, 1.0, "ASTS", "Direct-to-cell\nHigh beta lottery", 4.55),
        (3.55, 2.55, 1.55, 1.0, "IRDM", "Mature satcom\nLower beta", 4.55),
        # Bucket C
        (6.4, 4.05, 1.55, 1.0, "LMT", "Space $13B segment\nCSP-friendly", 7.4),
        (6.4, 2.75, 1.55, 1.0, "RTX/NOC", "Missiles + backlog\nSteady uptrend", 7.4),
        # Bucket D
        (9.25, 3.55, 1.55, 1.0, "VRT/CEG", "Cooling + nuclear\nxAI capex spillover", 10.25),
    ]
    parent_y = {1.7: 5.55, 4.55: 5.05, 7.4: 5.35, 10.25: 4.75}
    for x, y, w, h, ticker, desc, parent_x in leaves:
        box(x, y, w, h, ticker, desc, fc=palette["leaf"], fs=9, bold=True)
        arrow(parent_x, parent_y[parent_x], x + w / 2, y + h)

    # Avoid zone
    box(
        0.5,
        0.45,
        11.0,
        1.05,
        "AVOID",
        "SPCE · RDW · Space ETFs (UFO/ARKX) · IPO underwriters · Chasing extended names",
        fc="#FEF2F2",
        ec="#EF4444",
        fs=10,
        bold=True,
    )

    ax.text(
        6.0,
        0.12,
        "Second-Order Winners Map  ·  SpaceX IPO Spillover  ·  May 2026",
        ha="center",
        fontsize=9,
        color=palette["subtext"],
    )
    return _save_fig("bucket_tree.png")


def draw_revenue_chart() -> Path:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.2))
    fig.patch.set_facecolor("white")

    segments = ["Starlink", "Launch", "xAI"]
    revenue = [11.4, 4.1, 3.2]
    profit = [4.4, None, -14.0]
    seg_colors = ["#2563EB", "#059669", "#EF4444"]

    bars = ax1.bar(segments, revenue, color=seg_colors, edgecolor="white", linewidth=1.2)
    ax1.set_title("2025 Revenue by Segment ($B)", fontsize=11, fontweight="bold", pad=10)
    ax1.set_ylabel("$ Billions")
    ax1.set_ylim(0, 14)
    ax1.spines[["top", "right"]].set_visible(False)
    for bar, val in zip(bars, revenue):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.25, f"${val}B", ha="center", fontsize=9, fontweight="bold")

    labels = ["Adj. EBITDA", "GAAP Net Loss", "xAI Cash Burn"]
    values = [6.6, -4.9, -14]
    ax2.barh(labels, values, color=["#059669", "#EF4444", "#B91C1C"])
    ax2.set_title("Profitability Snapshot ($B)", fontsize=11, fontweight="bold", pad=10)
    ax2.set_xlabel("$ Billions")
    ax2.set_xlim(-16, 8)
    ax2.axvline(0, color="#64748B", linewidth=0.8)
    ax2.spines[["top", "right"]].set_visible(False)
    for i, val in enumerate(values):
        if val > 0:
            ax2.text(val + 0.35, i, f"${val}B", va="center", ha="left", fontsize=9, fontweight="bold")
        else:
            # Place negative labels on the right (positive x side) to avoid bar overlap
            ax2.text(0.6, i, f"-${abs(val)}B", va="center", ha="left", fontsize=9, fontweight="bold", color="#B91C1C")

    plt.tight_layout()
    return _save_fig("revenue_chart.png")


def draw_ranked_watchlist() -> Path:
    fig, ax = plt.subplots(figsize=(10, 5.2))
    fig.patch.set_facecolor("white")

    # Entry Readiness = thesis quality adjusted for current timing (pullback vs extended)
    names = ["VRT", "LMT", "CEG", "PL", "IRDM", "RKLB", "ASTS"]
    scores = [90, 88, 86, 84, 78, 68, 55]
    bar_colors = ["#D97706", "#059669", "#D97706", "#2563EB", "#7C3AED", "#2563EB", "#EF4444"]
    rationale = [
        "Pullback-ready · AI infra",
        "CSP-friendly · steady",
        "Power scarcity play",
        "Backlog growth · less extended",
        "Quality satellite",
        "Best thesis · wait for dip (+57%)",
        "Deep dip only",
    ]

    y = range(len(names))
    bars = ax.barh(list(y), scores, color=bar_colors, height=0.65, edgecolor="white")
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=10, fontweight="bold")
    ax.invert_yaxis()
    ax.set_xlim(0, 105)
    ax.set_xlabel("Entry Readiness Score (thesis quality + timing)")
    ax.set_title("Ranked Watchlist — Entry Readiness (Not Thesis Quality Alone)", fontsize=11, fontweight="bold", pad=12)
    ax.spines[["top", "right"]].set_visible(False)

    for bar, score, note in zip(bars, scores, rationale):
        ax.text(score + 1.5, bar.get_y() + bar.get_height() / 2, f"{score}  ·  {note}", va="center", fontsize=8, color="#334155")

    legend_patches = [
        mpatches.Patch(color="#2563EB", label="Launch / Space"),
        mpatches.Patch(color="#7C3AED", label="Satellite"),
        mpatches.Patch(color="#059669", label="Defense"),
        mpatches.Patch(color="#D97706", label="AI Infra"),
        mpatches.Patch(color="#EF4444", label="Speculative"),
    ]
    fig.subplots_adjust(bottom=0.22)
    ax.legend(
        handles=legend_patches,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.10),
        ncol=5,
        fontsize=8,
        framealpha=0.9,
    )
    ax.text(
        0.5,
        -0.22,
        "RKLB ranks #1 on thesis quality (best public SpaceX comp) but lower here because it is extended +57%. "
        "Higher score = better setup to enter now.",
        ha="center",
        fontsize=7.5,
        color="#64748B",
        transform=ax.transAxes,
    )
    return _save_fig("watchlist.png")


def draw_timeline() -> Path:
    fig, ax = plt.subplots(figsize=(10, 2.8))
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3)
    ax.axis("off")

    events = [
        (1.0, "Apr 1", "Confidential\nS-1 filed"),
        (3.3, "May 20", "Public S-1\nreleased"),
        (5.6, "Late Jun", "SPCX pricing\n(volatility peak)"),
        (7.9, "Q3 2026", "RKLB Neutron\nfirst launch"),
    ]
    ax.plot([0.8, 9.2], [1.5, 1.5], color="#CBD5E1", linewidth=3, zorder=1)
    for x, date_label, desc in events:
        circle = plt.Circle((x, 1.5), 0.18, color="#2563EB", zorder=3)
        ax.add_patch(circle)
        ax.text(x, 1.5, "●", ha="center", va="center", fontsize=14, color="white", zorder=4)
        ax.text(x, 2.15, date_label, ha="center", fontsize=9, fontweight="bold", color="#1E3A5F")
        ax.text(x, 0.65, desc, ha="center", fontsize=8, color="#475569")

    ax.text(5, 2.65, "Key Catalyst Timeline", ha="center", fontsize=11, fontweight="bold", color="#0F172A")
    return _save_fig("timeline.png")


def _playbook_rows() -> list[tuple[str, str, str, str]]:
    return [
        (
            "Conservative uptrend",
            "LMT, RTX, VRT, CEG",
            "Wait for the stock to dip toward its 8- or 21-day moving average "
            "(a short-term trend line that tracks recent price). Then sell cash-secured "
            "puts: you collect premium upfront and only buy shares if the price falls "
            "to your chosen strike.",
            "Low",
        ),
        (
            "Growth on pullback",
            "PL, RKLB (on dip)",
            "Do not buy at today's price. Wait for a post-IPO sector selloff or a dip "
            "toward the 21-day moving average, then buy shares. RKLB has a strong thesis "
            "but is already up 57% — patience is required.",
            "Medium",
        ),
        (
            "High beta lottery",
            "ASTS",
            "Only consider after a sharp price drop (10%+). If you buy shares, sell "
            "covered calls against them — this means selling call options on stock you "
            "already own to earn monthly premium while holding a volatile position.",
            "High",
        ),
        (
            "Timing gate",
            "Extended names",
            "Some names belong on your watchlist but not in your portfolio today. "
            "RKLB has run +57% on IPO excitement — monitor it and wait for a meaningful "
            "pullback before entering.",
            "—",
        ),
    ]


def build_playbook_table(styles) -> Table:
    """Native ReportLab table — Paragraph cells wrap text properly."""
    cell = ParagraphStyle(
        "PlaybookCell",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=12,
        alignment=TA_LEFT,
    )
    header = ParagraphStyle(
        "PlaybookHeader",
        parent=cell,
        fontName="Helvetica-Bold",
        textColor=colors.white,
        fontSize=9,
    )
    risk_styles = {
        "Low": ParagraphStyle("RiskLow", parent=cell, textColor=colors.HexColor("#059669"), fontName="Helvetica-Bold"),
        "Medium": ParagraphStyle("RiskMed", parent=cell, textColor=colors.HexColor("#D97706"), fontName="Helvetica-Bold"),
        "High": ParagraphStyle("RiskHigh", parent=cell, textColor=colors.HexColor("#EF4444"), fontName="Helvetica-Bold"),
        "—": ParagraphStyle("RiskDash", parent=cell, textColor=colors.HexColor("#64748B"), fontName="Helvetica-Bold"),
    }
    row_colors = ["#ECFDF5", "#EFF6FF", "#FEF3C7", "#FEF2F2"]

    table_rows = [
        [
            Paragraph("Strategy", header),
            Paragraph("Tickers", header),
            Paragraph("Recommended Action", header),
            Paragraph("Risk", header),
        ]
    ]
    for strategy, tickers, action, risk in _playbook_rows():
        table_rows.append(
            [
                Paragraph(strategy, cell),
                Paragraph(tickers, cell),
                Paragraph(action, cell),
                Paragraph(risk, risk_styles[risk]),
            ]
        )

    col_widths = [1.15 * inch, 1.05 * inch, 4.05 * inch, 0.55 * inch]
    table = Table(table_rows, colWidths=col_widths)
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for i, color in enumerate(row_colors, start=1):
        style_commands.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor(color)))
    table.setStyle(TableStyle(style_commands))
    return table


def build_pdf(images: dict[str, Path]) -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title="SpaceX IPO — Second-Order Winners",
        author="Tyche Options Research",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleCustom",
        parent=styles["Title"],
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#1E3A5F"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=11,
        textColor=colors.HexColor("#64748B"),
        spaceAfter=14,
    )
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=14, textColor=colors.HexColor("#1E3A5F"), spaceBefore=10, spaceAfter=8)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=11.5, textColor=colors.HexColor("#334155"), spaceBefore=8, spaceAfter=6)
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9.5, leading=14, alignment=TA_JUSTIFY, spaceAfter=6)
    bullet = ParagraphStyle("Bullet", parent=body, leftIndent=14, bulletIndent=0, spaceAfter=4)

    def img(path: Path, width: float) -> Image:
        return Image(str(path), width=width, height=width * 0.55 if "tree" in path.name else width * 0.42)

    story = []

    # Cover
    story.append(Paragraph("SpaceX S-1 Analysis", title_style))
    story.append(Paragraph("Second-Order Winners — Who Rides the Uptrend Without Buying SPCX", subtitle_style))
    story.append(
        Paragraph(
            f"<b>Report Date:</b> {date.today():%B %d, %Y} &nbsp;|&nbsp; "
            "<b>Subject:</b> Space Exploration Technologies Corp. (CIK 0001181412) &nbsp;|&nbsp; "
            "<b>Ticker:</b> SPCX (expected)",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 0.1 * inch))
    story.append(
        Paragraph(
            "SpaceX filed the largest IPO registration statement in history on May 20, 2026 — targeting "
            "a $1.75–2.0T valuation and up to $75B in proceeds. This report maps the investment landscape "
            "for investors who prefer to avoid SPCX volatility and instead ride validated second-order winners.",
            body,
        )
    )
    story.append(Spacer(1, 0.15 * inch))

    # Executive summary table
    summary_data = [
        ["Metric", "Value"],
        ["Target Valuation", "$1.75 – 2.0+ Trillion"],
        ["Capital Raise", "Up to $75 Billion"],
        ["2025 Revenue", "$18.7B (+43% YoY)"],
        ["2025 GAAP Net Loss", "-$4.9 Billion"],
        ["2025 Adj. EBITDA", "$6.6 Billion"],
        ["Revenue Multiple (at IPO)", "~95x Trailing Revenue"],
        ["Prior IPO Record (Aramco 2019)", "$29.4 Billion"],
    ]
    t = Table(summary_data, colWidths=[2.4 * inch, 4.4 * inch])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(t)
    story.append(PageBreak())

    # Bucket tree
    story.append(Paragraph("The Winning Map — Bucketed Tree", h1))
    story.append(
        Paragraph(
            "The SpaceX IPO validates two mega-theses: (1) space and satellite connectivity as profitable "
            "infrastructure, and (2) massive AI capex that reprices the entire compute stack. Quality names "
            "with backlog and execution get dragged up; hype names get exposed.",
            body,
        )
    )
    story.append(Spacer(1, 0.1 * inch))
    story.append(Image(str(images["tree"]), width=6.9 * inch, height=5.2 * inch))
    story.append(PageBreak())

    # Financials
    story.append(Paragraph("SpaceX Financial Snapshot", h1))
    story.append(Image(str(images["revenue"]), width=6.5 * inch, height=2.8 * inch))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph("Segment Breakdown", h2))
    seg_data = [
        ["Segment", "Revenue", "Growth", "Profitability"],
        ["Starlink", "$11.4B (61%)", "+48% YoY", "$4.4B operating profit"],
        ["Launch Services", "$4.1B (22%)", "+8% YoY", "Profitable (gov contracts)"],
        ["xAI", "$3.2B (17%)", "+22% YoY", "~$14B cash burn"],
    ]
    seg_table = Table(seg_data, colWidths=[1.5 * inch, 1.5 * inch, 1.2 * inch, 2.6 * inch])
    seg_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563EB")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC"), colors.HexColor("#FEF2F2")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(seg_table)
    story.append(Spacer(1, 0.15 * inch))
    story.append(
        Paragraph(
            "<b>Key insight:</b> Starlink is the financial engine. xAI is the highest-risk, highest-optionality "
            "bet — absorbing 60% of capex (~$20B) while generating only 17% of revenue. The entire $4.9B net "
            "loss is attributable to xAI.",
            body,
        )
    )
    story.append(PageBreak())

    # Tier analysis
    story.append(Paragraph("Tier 1 — Highest-Conviction Winners", h1))

    tiers = [
        (
            "Rocket Lab (RKLB) — The Public SpaceX Proxy",
            "Closest pure-play to SpaceX's launch + spacecraft stack. 2025 revenue ~$602M (+38%), backlog "
            "~$1.85–2.2B (+73%). Neutron heavy-lift targeting Q4 2026. Already +57% since IPO rumors — "
            "wait for pullback to 21-EMA, not a chase.",
        ),
        (
            "Planet Labs (PL) — The Boring Winner",
            "Earth observation with proven recurring revenue. FY2026 revenue ~$307M (+26%), backlog ~$900M (+79%). "
            "Less direct Starlink competition than ASTS. Government + commercial imagery demand is structural.",
        ),
        (
            "Lockheed Martin (LMT) / RTX / Northrop (NOC) — Defense Space",
            "LMT space segment: $13B revenue (+4%), guided $13.5–13.8B for 2026. NOC: $95B+ backlog. "
            "Lower volatility, dividend, options liquidity. Best for CSP / covered-call playbook.",
        ),
    ]
    for title, text in tiers:
        story.append(Paragraph(title, h2))
        story.append(Paragraph(text, body))

    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph("Tier 2 — Satellite Connectivity (Higher Beta)", h1))
    story.append(
        Paragraph(
            "<b>AST SpaceMobile (ASTS):</b> Direct-to-cell from space with AT&T/Verizon partnerships. "
            "Speculative winner — high beta lottery ticket only on deep pullbacks.<br/><br/>"
            "<b>Iridium (IRDM):</b> Mature satcom with recurring revenue. Often rises when speculative "
            "space names fall — quality flight within the sector.",
            body,
        )
    )

    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph("Tier 3 — AI Infrastructure Pick-and-Shovel", h1))
    infra_data = [
        ["Ticker", "Role", "Thesis"],
        ["VRT", "Data center cooling", "Every GW of AI compute needs thermal management"],
        ["CEG", "Nuclear baseload", "AI power scarcity → premium for 24/7 carbon-free power"],
        ["NEE", "Utility + renewables", "Grid interconnection bottleneck; Meta 2.5 GW deal"],
        ["NVDA / AVGO", "AI chips", "xAI burn = more GPU demand"],
        ["EQIX / DLR", "Data center REITs", "Land + power + connectivity; less binary than space"],
    ]
    infra_table = Table(infra_data, colWidths=[0.9 * inch, 1.6 * inch, 4.3 * inch])
    infra_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D97706")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FFFBEB")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(infra_table)
    story.append(PageBreak())

    # Watchlist + timeline + playbook
    story.append(Paragraph("Ranked Watchlist — Entry Readiness", h1))
    story.append(
        Paragraph(
            "<b>How to read this chart:</b> Scores reflect <i>entry readiness</i> — thesis quality combined with "
            "current timing. RKLB is the strongest SpaceX proxy by thesis, but ranks lower here because it has "
            "already run +57% and is not a buy-today setup. VRT and LMT rank higher because they offer the same "
            "sector tailwind with better pullback entry conditions.",
            body,
        )
    )
    story.append(Spacer(1, 0.08 * inch))
    story.append(Image(str(images["watchlist"]), width=6.5 * inch, height=3.6 * inch))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("Key Catalyst Timeline", h1))
    story.append(Image(str(images["timeline"]), width=6.5 * inch, height=1.9 * inch))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("Practical Playbook Matrix", h1))
    story.append(build_playbook_table(styles))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("What to Avoid", h1))
    avoid_items = [
        "Virgin Galactic (SPCE) — no meaningful revenue",
        "Redwire (RDW) — turnaround with execution risk",
        "Intuitive Machines (LUNR) — NASA lottery, very volatile",
        "Space ETFs (UFO, ARKX) — dilute winners with weak holdings; already +30% YTD",
        "IPO underwriters (MS, GS) — one-time fee pop, not sustained uptrend",
        "Chasing extended names — RKLB +57% (watchlist, not buy today), ASTS on Starlink hype",
    ]
    for item in avoid_items:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("Bottom Line", h1))
    story.append(
        Paragraph(
            "SPCX validates the sector — it does not lift all boats equally. Winners have revenue, backlog, "
            "and execution. The edge is not buying the IPO; it is riding the re-rating in quality names on "
            "pullbacks while avoiding the post-IPO volatility window. RKLB is the best thesis proxy but requires "
            "a dip entry; VRT and CEG offer cleaner near-term setups with the same AI capex tailwind.",
            body,
        )
    )

    story.append(Spacer(1, 0.3 * inch))
    disclaimer = ParagraphStyle("Disclaimer", parent=body, fontSize=7.5, textColor=colors.HexColor("#94A3B8"), alignment=TA_JUSTIFY)
    story.append(
        Paragraph(
            "<b>Disclaimer:</b> This report is for informational purposes only and does not constitute "
            "investment advice, a recommendation, or a solicitation to buy or sell any security. All figures "
            "reflect publicly reported estimates and SEC filing data as of May 2026. Past performance does not "
            "guarantee future results. Conduct your own due diligence before making investment decisions.",
            disclaimer,
        )
    )

    doc.build(story)
    return OUTPUT


def main() -> None:
    print("Generating visuals...")
    images = {
        "tree": draw_bucket_tree(),
        "revenue": draw_revenue_chart(),
        "watchlist": draw_ranked_watchlist(),
        "timeline": draw_timeline(),
    }
    print("Building PDF...")
    path = build_pdf(images)
    print(f"Report saved to: {path}")


if __name__ == "__main__":
    main()
