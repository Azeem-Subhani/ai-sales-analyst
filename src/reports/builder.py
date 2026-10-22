"""Builds the scheduled reports as HTML, chart PNGs and Excel (plan sections 7 and 8).

`build_report(report_name, as_of)` reads the report's block in config/reports.yaml,
runs the KPI queries in src/analytics/kpis.py against a read-only connection and
returns a Report. Every number shown in the HTML or Excel comes from `Report.numbers`,
so Week 4 can pass exactly those numbers to the LLM summary and Week 3 can email the
HTML with the chart images attached inline (cid: references).

Each report starts with a short LLM-written summary of these numbers
(src/reports/summary.py); if the model is unavailable or the summary fails the number
guard, the report is built anyway with "Summary unavailable for this run".

Dry run from the command line (writes to outputs/reports/<report>_<as_of>/):
    python -m src.reports.builder --report weekly_sales_review --as-of 2026-09-27
    python -m src.reports.builder --all --as-of 2026-09-30
    python -m src.reports.builder --all --as-of 2026-09-30 --no-summary   # no LLM call
"""

import argparse
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import matplotlib

matplotlib.use("Agg")  # no screen needed; must be set before pyplot is imported

import duckdb
import matplotlib.pyplot as plt
import pandas as pd
import yaml
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from matplotlib.ticker import FuncFormatter, PercentFormatter
from openpyxl.utils import get_column_letter

from src.analytics import kpis

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "reports.yaml"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "reports"
TEMPLATE_DIR = Path(__file__).with_name("templates")
TEMPLATE_NAME = "report.html"
HTML_FILENAME = "report.html"
# Top-level keys in reports.yaml that are settings, not reports.
RESERVED_CONFIG_KEYS = {"delivery"}

SUMMARY_PLACEHOLDER = "Summary unavailable for this run"

TOP_N = 10
DAILY_TOP_N = 5
TREND_WEEKS = 13
TREND_MONTHS = 12

# Rounding applied once, when numbers enter the Report (display and Excel use these).
MONEY_DECIMALS = 2
MONEY_STEP = Decimal(1).scaleb(-MONEY_DECIMALS)  # 0.01
# Rates and changes are not rounded when stored: they are rounded once, when shown
# (fmt_pct, fmt_change, Excel number formats). Rounding them on the way in as well
# turned a true +8.6495% into "+8.7%" (Week 4 wrap-up, docs/decisions.md).
UNROUNDED_KINDS = {"rate", "change"}

# KPIs shown in the daily snapshot (plan section 7: revenue, orders, AOV, cancellations).
DAILY_KPIS = ["net_revenue", "orders", "aov", "units", "cancelled_orders", "cancellation_rate"]
# For these KPIs a fall is good news; the template colours changes accordingly.
LOWER_IS_BETTER = {
    "cost_of_goods",
    "returns_value",
    "cancelled_orders",
    "cancellation_rate",
    "returned_orders",
    "return_rate",
}

COMPARISON_LABELS = {
    "same_weekday_last_week": "same day last week",
    "previous_week": "previous week",
    "same_week_last_year": "same week last year",
    "previous_month": "previous month",
    "same_month_last_year": "same month last year",
}
DIMENSION_LABELS = {
    "category": "Category",
    "region": "Region",
    "channel": "Channel",
    "customer_type": "Customer type",
}
EXCEL_SHEETS = {  # section name -> sheet name
    "kpis": "KPIs",
    "category": "Categories",
    "channel": "Channels",
    "region": "Regions",
    "customer_type": "Customer types",
    "top_products": "Top products",
    "top_customers": "Top customers",
}
EXCEL_MAX_COLUMN_WIDTH = 50
EXCEL_COLUMN_PADDING = 2
EXCEL_FORMATS = {
    "money": "#,##0.00",
    "count": "#,##0",
    "rate": "0.0%",
    "change": "+0.0;-0.0;0.0",
    "days": "0.0",
}

# Chart look: one blue series for the current period, warm gray for the comparison,
# neutral ink for text (dataviz reference palette, light mode; emails render on white).
CHART_DPI = 200  # 2x the 100 dpi base, so charts stay sharp on phones and retina screens
CHART_SIZE_INCHES = (6.0, 2.8)
CHART_DISPLAY_WIDTH_PX = 600
COLOR_CURRENT = "#2a78d6"
COLOR_PREVIOUS = "#b4b2a9"
COLOR_INK = "#0b0b0b"
COLOR_INK_MUTED = "#52514e"
COLOR_GRID = "#e4e2dc"
LINE_WIDTH = 2
MARKER_SIZE = 4
THOUSAND = 1_000
TREND_LABEL_EVERY = 2  # x-axis label spacing on trend charts, so dates never collide
TREND_HEADROOM = 1.12  # y-axis top as a multiple of the highest value


@dataclass
class Report:
    """A built report, ready to save (dry run) or email (Week 3).

    html references charts as cid:<chart name>; images maps that name to PNG bytes.
    numbers holds every value shown, rounded, as JSON-safe types.
    """

    name: str
    title: str
    as_of: date
    subject: str
    html: str
    images: dict[str, bytes]
    excel_path: Path | None
    numbers: dict
    output_dir: Path
    summary_status: str | None = None  # ok | guard_failed | truncated | llm_unavailable | skipped
    summary_cost_usd: float | None = None  # OpenRouter cost of the summary calls, if reported


# ---------------------------------------------------------------- config


def read_config_file(config_path: Path = CONFIG_PATH) -> dict:
    """The whole of config/reports.yaml, including the non-report `delivery` block."""
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def load_config(config_path: Path = CONFIG_PATH) -> dict[str, dict]:
    """Read the report blocks of config/reports.yaml and check every name in them is known."""
    config = {
        name: block
        for name, block in read_config_file(config_path).items()
        if name not in RESERVED_CONFIG_KEYS
    }
    for name, block in config.items():
        validate_report_config(name, block)
    return config


def validate_report_config(name: str, block: dict) -> None:
    """Raise ValueError if a report block uses an unknown period, query, comparison or chart."""
    if block.get("period") not in kpis.PERIOD_KINDS:
        raise ValueError(f"{name}: period must be one of {kpis.PERIOD_KINDS}.")
    for query in block.get("queries", []):
        if query not in QUERY_SECTIONS:
            raise ValueError(f"{name}: unknown query {query!r}.")
    for chart in block.get("charts", []):
        if chart not in CHARTS:
            raise ValueError(f"{name}: unknown chart {chart!r}.")
    compare_to = block.get("compare_to", [])
    if not compare_to:
        raise ValueError(f"{name}: compare_to needs at least one comparison.")
    for comparison in compare_to:
        if kpis.COMPARISONS.get(comparison) != block["period"]:
            raise ValueError(f"{name}: comparison {comparison!r} does not fit a {block['period']}.")


# ---------------------------------------------------------------- rounding and formatting


def rounded(value: float | None, kind: str) -> float | int | None:
    """A value as stored in the Report, by kind: money to cents, counts whole.

    Rates and changes are returned unrounded, so the only rounding is at display.
    """
    value = kpis.none_if_nan(value)
    if value is None:
        return None
    if kind == "count":
        return round(value)
    if kind in UNROUNDED_KINDS:
        return value
    if kind == "money":
        return round_money(value)
    raise ValueError(f"Unknown value kind {kind!r}.")


def round_money(value: float) -> float:
    """Money to cents, half-up, from the exact decimal the SQL sum produced.

    DuckDB sums DECIMAL columns exactly (August 2026 net revenue is 462,431.555), but
    pandas hands the sum over as a float, and Python's round() works on the binary
    value just below it, giving 462,431.55. str() of the float gives back the exact
    decimal (sums here have at most 15 significant digits), and that is rounded
    half-up like SQL ROUND, giving 462,431.56.
    """
    exact = Decimal(str(value))
    return float(exact.quantize(MONEY_STEP, rounding=ROUND_HALF_UP))


def fmt_money(value: float | None) -> str:
    """12345.6 -> '12,345.60'."""
    return "n/a" if value is None else f"{value:,.2f}"


def fmt_count(value: float | None) -> str:
    """12345 -> '12,345'."""
    return "n/a" if value is None else f"{value:,.0f}"


def fmt_days(value: float | None) -> str:
    """Days with one decimal: 9.087 -> '9.1' (days of cover, usual order gap)."""
    return "n/a" if value is None else f"{value:,.1f}"


def fmt_pct(value: float | None) -> str:
    """A rate stored as a fraction: 0.4314 -> '43.1%'."""
    return "n/a" if value is None else f"{value * 100:.1f}%"


def fmt_change(value: float | None, unit: str) -> str:
    """A change with an explicit sign: '+2.5%' or '-1.6 pp' (unit is 'pct' or 'pp')."""
    if value is None:
        return "n/a"
    return f"{value:+.1f}%" if unit == "pct" else f"{value:+.1f} pp"


FORMATTERS: dict[str, Callable[[float | None], str]] = {
    "money": fmt_money,
    "count": fmt_count,
    "rate": fmt_pct,
}


def change_unit(kind: str) -> str:
    """Rates change in percentage points, everything else in percent."""
    return "pp" if kind == "rate" else "pct"


def tone(change: float | None, lower_is_better: bool = False) -> str:
    """'good', 'bad' or 'flat' for colouring a change (signs carry the meaning too)."""
    if change is None or round(change, 1) == 0:
        return "flat"
    rising = change > 0
    return "good" if rising != lower_is_better else "bad"


def cell(text: str, align: str = "right", tone_name: str = "flat") -> dict[str, str]:
    """One table cell for the template."""
    return {"text": text, "align": align, "tone": tone_name}


def change_cell(value: float | None, unit: str, lower_is_better: bool = False) -> dict[str, str]:
    """A signed change cell, coloured by whether the move is good news."""
    return cell(fmt_change(value, unit), tone_name=tone(value, lower_is_better))


# ---------------------------------------------------------------- report context


@dataclass
class Context:
    """Everything a section needs: the connection, period and its comparisons."""

    con: duckdb.DuckDBPyConnection
    period: kpis.Period
    comparisons: dict[str, kpis.Period]
    as_of: date | None = None
    db_path: Path = kpis.DB_PATH
    demand: Any = None  # the demand snapshot, loaded by the first section that needs it

    @property
    def primary(self) -> str:
        """The first comparison, used for change columns in breakdowns and top lists."""
        return next(iter(self.comparisons))


def period_info(period: kpis.Period) -> dict[str, str]:
    """JSON-safe description of a period."""
    return {
        "kind": period.kind,
        "start": period.start.isoformat(),
        "end": period.end.isoformat(),
        "label": period.label,
    }


# ---------------------------------------------------------------- sections (numbers)


def kpi_section(ctx: Context, keys: list[str], title: str = "Key figures") -> dict:
    """Headline KPIs for the period and each comparison."""
    table = kpis.kpi_table(ctx.con, ctx.period, ctx.comparisons).set_index("kpi")
    rows = []
    for key in keys:
        row = table.loc[key]
        rows.append(
            {
                "kpi": key,
                "label": row["label"],
                "kind": row["kind"],
                "current": rounded(row["current"], row["kind"]),
                "comparisons": {
                    name: {
                        "value": rounded(row[name], row["kind"]),
                        "change": rounded(row[f"{name}_change"], "change"),
                    }
                    for name in ctx.comparisons
                },
            }
        )
    return {"type": "kpis", "title": title, "rows": rows}


def breakdown_section(ctx: Context, dimension: str, title: str, show_margin: bool) -> dict:
    """Net revenue by one dimension with change vs the primary comparison."""
    df = kpis.breakdown(ctx.con, dimension, ctx.period, ctx.comparisons[ctx.primary])
    rows = [
        {
            "value": r["value"],
            "net_revenue": rounded(r["net_revenue"], "money"),
            "share": rounded(r["share"], "rate"),
            "gross_margin_pct": rounded(r["gross_margin_pct"], "rate"),
            "previous_net_revenue": rounded(r["previous_net_revenue"], "money"),
            "previous_gross_margin_pct": rounded(r["previous_gross_margin_pct"], "rate"),
            "net_revenue_change": rounded(r["net_revenue_change"], "change"),
            "gross_margin_change": rounded(r["gross_margin_change"], "change"),
        }
        for r in df.to_dict("records")
    ]
    return {
        "type": "breakdown",
        "title": title,
        "dimension": dimension,
        "compared_to": ctx.primary,
        "show_margin": show_margin,
        "rows": rows,
    }


def top_section(ctx: Context, entity: str, n: int) -> dict:
    """Top n products or customers with change, plus last period's top n that dropped out."""
    if entity == "products":
        top, dropped = kpis.top_products(ctx.con, ctx.period, ctx.comparisons[ctx.primary], n)
        name_col, detail_col, volume_col = "product_name", "category", "units"
    else:
        top, dropped = kpis.top_customers(ctx.con, ctx.period, ctx.comparisons[ctx.primary], n)
        name_col, detail_col, volume_col = "customer_name", "customer_type", "orders"
    rows = [
        {
            "rank": int(r["rank"]),
            "name": r[name_col],
            "detail": r[detail_col],
            "net_revenue": rounded(r["net_revenue"], "money"),
            "volume": rounded(r[volume_col], "count"),
            "previous_net_revenue": rounded(r["previous_net_revenue"], "money"),
            "net_revenue_change": rounded(r["net_revenue_change"], "change"),
        }
        for r in top.to_dict("records")
    ]
    dropped_rows = [
        {
            "name": r[name_col],
            "detail": r[detail_col],
            "previous_rank": int(r["previous_rank"]),
            "previous_net_revenue": rounded(r["previous_net_revenue"], "money"),
            "current_net_revenue": rounded(r["current_net_revenue"], "money"),
            "current_rank": rounded(r["current_rank"], "count"),
            "net_revenue_change": rounded(r["net_revenue_change"], "change"),
        }
        for r in dropped.to_dict("records")
    ]
    return {
        "type": "top",
        "title": f"Top {n} {entity} by net revenue",
        "entity": entity,
        "n": n,
        "volume_label": "Units" if entity == "products" else "Orders",
        "compared_to": ctx.primary,
        "rows": rows,
        "dropped_out": dropped_rows,
        "dropped_title": f"Dropped out of the top {n} since the {COMPARISON_LABELS[ctx.primary]}",
    }


def customer_growth_section(ctx: Context) -> dict:
    """Active, new and returning customers, and the new-customer share."""
    current = kpis.kpi_values(ctx.con, ctx.period)
    others = {name: kpis.kpi_values(ctx.con, p) for name, p in ctx.comparisons.items()}

    def growth(values: dict) -> dict[str, float | None]:
        new, returning = values["new_customers"] or 0, values["returning_customers"] or 0
        active = new + returning
        return {
            "active_customers": active,
            "new_customers": new,
            "returning_customers": returning,
            "new_share": new / active if active else None,
        }

    labels = {
        "active_customers": ("Active customers", "count"),
        "new_customers": ("New customers", "count"),
        "returning_customers": ("Returning customers", "count"),
        "new_share": ("New customer share", "rate"),
    }
    now = growth(current)
    then = {name: growth(values) for name, values in others.items()}
    rows = [
        {
            "kpi": key,
            "label": label,
            "kind": kind,
            "current": rounded(now[key], kind),
            "comparisons": {
                name: {
                    "value": rounded(then[name][key], kind),
                    "change": rounded(kpis.change(now[key], then[name][key], kind), "change"),
                }
                for name in ctx.comparisons
            },
        }
        for key, (label, kind) in labels.items()
    ]
    return {"type": "kpis", "title": "Customer growth", "rows": rows}


def placeholder_section(title: str, note: str) -> dict:
    """An empty, clearly marked section that a later week fills in."""
    return {"type": "placeholder", "title": title, "note": note}


def demand_sections() -> ModuleType:
    """src.reports.demand_sections, imported on use (it uses this module's formatters)."""
    from src.reports import demand_sections as module

    return module


# Query name in reports.yaml -> function building that section's numbers.
QUERY_SECTIONS: dict[str, Callable[[Context], dict]] = {
    "kpis_day": lambda ctx: kpi_section(ctx, DAILY_KPIS),
    "kpis_week": lambda ctx: kpi_section(ctx, list(kpis.KPIS)),
    "kpis_month": lambda ctx: kpi_section(ctx, list(kpis.KPIS)),
    "category_breakdown": lambda ctx: breakdown_section(
        ctx, "category", "Net revenue by category", show_margin=False
    ),
    "margin_by_category": lambda ctx: breakdown_section(
        ctx, "category", "Net revenue and gross margin by category", show_margin=True
    ),
    "channel_breakdown": lambda ctx: breakdown_section(
        ctx, "channel", "Net revenue by channel", show_margin=False
    ),
    "region_breakdown": lambda ctx: breakdown_section(
        ctx, "region", "Net revenue by region", show_margin=False
    ),
    "customer_type_breakdown": lambda ctx: breakdown_section(
        ctx, "customer_type", "Net revenue by customer type", show_margin=False
    ),
    "top_products": lambda ctx: top_section(ctx, "products", TOP_N),
    "top_products_5": lambda ctx: top_section(ctx, "products", DAILY_TOP_N),
    "top_customers": lambda ctx: top_section(ctx, "customers", TOP_N),
    "customer_growth": customer_growth_section,
    "demand_alerts": lambda ctx: demand_sections().demand_alerts_section(ctx),
    "forecast_accuracy": lambda ctx: demand_sections().forecast_accuracy_section(ctx),
}


# ---------------------------------------------------------------- charts


def new_axes() -> tuple[plt.Figure, plt.Axes]:
    """A figure with recessive grid and axes in the report's chart style."""
    fig, ax = plt.subplots(figsize=CHART_SIZE_INCHES, dpi=CHART_DPI)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(COLOR_GRID)
    ax.tick_params(colors=COLOR_INK_MUTED, labelsize=8, length=0)
    ax.grid(color=COLOR_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def to_png(fig: plt.Figure) -> bytes:
    """Render a figure to PNG bytes and free it."""
    buffer = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buffer, format="png", dpi=CHART_DPI, facecolor="white")
    plt.close(fig)
    return buffer.getvalue()


def thousands(value: float, _position: int) -> str:
    """Axis label: 104527 -> '105k'."""
    return f"{value / THOUSAND:,.0f}k"


def trend_chart(trend: pd.DataFrame, date_format: str) -> bytes:
    """Line chart of net revenue per bucket, with the latest bucket marked."""
    fig, ax = new_axes()
    ax.grid(axis="x", visible=False)
    x = list(range(len(trend)))
    ax.plot(x, trend["net_revenue"], color=COLOR_CURRENT, linewidth=LINE_WIDTH)
    ax.plot(x[-1], trend["net_revenue"].iloc[-1], "o", color=COLOR_CURRENT, ms=MARKER_SIZE * 2)
    # Label every other bucket, counting back from the latest so it is always labelled.
    labelled = x[::-TREND_LABEL_EVERY][::-1]
    ax.set_xticks(labelled)
    ax.set_xticklabels(
        [pd.Timestamp(trend["bucket_start"].iloc[i]).strftime(date_format) for i in labelled]
    )
    ax.yaxis.set_major_formatter(FuncFormatter(thousands))
    ax.set_ylim(0, trend["net_revenue"].max() * TREND_HEADROOM)
    ax.set_ylabel("Net revenue", color=COLOR_INK_MUTED, fontsize=8)
    return to_png(fig)


def revenue_by_category_chart(section: dict, comparison_label: str) -> bytes:
    """Horizontal bars: net revenue by category, this period vs the comparison."""
    rows = section["rows"][::-1]  # largest at the top
    fig, ax = new_axes()
    ax.grid(axis="y", visible=False)
    y = list(range(len(rows)))
    height = 0.38
    ax.barh(
        [i - height / 2 for i in y],
        [r["previous_net_revenue"] or 0 for r in rows],
        height=height,
        color=COLOR_PREVIOUS,
        label=comparison_label.capitalize(),
    )
    ax.barh(
        [i + height / 2 for i in y],
        [r["net_revenue"] for r in rows],
        height=height,
        color=COLOR_CURRENT,
        label="This period",
    )
    ax.set_yticks(y)
    ax.set_yticklabels([r["value"] for r in rows], color=COLOR_INK)
    ax.xaxis.set_major_formatter(FuncFormatter(thousands))
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], labels[::-1], frameon=False, fontsize=8, loc="lower right")
    return to_png(fig)


def margin_by_category_chart(section: dict) -> bytes:
    """Horizontal bars: gross margin % by category, labelled at the bar ends."""
    rows = sorted(section["rows"], key=lambda r: r["gross_margin_pct"] or 0)
    fig, ax = new_axes()
    ax.grid(axis="y", visible=False)
    margins = [(r["gross_margin_pct"] or 0) * 100 for r in rows]
    bars = ax.barh([r["value"] for r in rows], margins, height=0.6, color=COLOR_CURRENT)
    ax.bar_label(
        bars, labels=[f"{m:.1f}%" for m in margins], padding=3, fontsize=7, color=COLOR_INK
    )
    ax.xaxis.set_major_formatter(PercentFormatter(decimals=0))
    ax.set_xlim(0, max(margins + [1]) * 1.15)
    ax.tick_params(axis="y", colors=COLOR_INK)
    return to_png(fig)


def build_trend(ctx: Context, bucket: str, count: int, date_format: str) -> tuple[bytes, dict]:
    """Trend chart PNG plus its numbers."""
    trend = kpis.revenue_trend(ctx.con, bucket, ctx.period.end, count)
    numbers = {
        "points": [
            {"bucket_start": pd.Timestamp(s).date().isoformat(), "net_revenue": rounded(v, "money")}
            for s, v in zip(trend["bucket_start"], trend["net_revenue"], strict=True)
        ]
    }
    return trend_chart(trend, date_format), numbers


def chart_revenue_trend_13w(ctx: Context, sections: dict) -> tuple[bytes, dict]:
    """13-week net revenue trend."""
    png, numbers = build_trend(ctx, "week", TREND_WEEKS, "%d %b")
    return png, {"title": f"Net revenue, last {TREND_WEEKS} weeks", **numbers}


def chart_revenue_trend_12m(ctx: Context, sections: dict) -> tuple[bytes, dict]:
    """12-month net revenue trend."""
    png, numbers = build_trend(ctx, "month", TREND_MONTHS, "%b %y")
    return png, {"title": f"Net revenue, last {TREND_MONTHS} months", **numbers}


def chart_revenue_by_category(ctx: Context, sections: dict) -> tuple[bytes, dict]:
    """Category revenue, this period vs the primary comparison (reuses the section numbers)."""
    section = sections.get("category_breakdown") or breakdown_section(
        ctx, "category", "Net revenue by category", show_margin=False
    )
    label = COMPARISON_LABELS[ctx.primary]
    return revenue_by_category_chart(section, label), {
        "title": f"Net revenue by category, this period vs {label}"
    }


def chart_margin_by_category(ctx: Context, sections: dict) -> tuple[bytes, dict]:
    """Gross margin by category for the period (reuses the section numbers)."""
    section = sections.get("margin_by_category") or breakdown_section(
        ctx, "category", "Net revenue and gross margin by category", show_margin=True
    )
    return margin_by_category_chart(section), {"title": "Gross margin by category"}


# Chart name in reports.yaml -> function returning (PNG bytes, chart numbers).
CHARTS: dict[str, Callable[[Context, dict], tuple[bytes, dict]]] = {
    "revenue_trend_13w": chart_revenue_trend_13w,
    "revenue_trend_12m": chart_revenue_trend_12m,
    "revenue_by_category": chart_revenue_by_category,
    "margin_by_category": chart_margin_by_category,
}


# ---------------------------------------------------------------- HTML view


def kpi_table_view(section: dict, comparisons: list[str]) -> dict:
    """Template table for a KPI-style section."""
    header = ["", "This period"]
    for name in comparisons:
        header += [COMPARISON_LABELS[name].capitalize(), "Change"]
    rows = []
    for row in section["rows"]:
        fmt = FORMATTERS[row["kind"]]
        cells = [cell(row["label"], "left"), cell(fmt(row["current"]))]
        for name in comparisons:
            comp = row["comparisons"][name]
            cells.append(cell(fmt(comp["value"])))
            cells.append(
                change_cell(comp["change"], change_unit(row["kind"]), row["kpi"] in LOWER_IS_BETTER)
            )
        rows.append(cells)
    return {"title": section["title"], "header": header, "rows": rows}


def breakdown_table_view(section: dict) -> dict:
    """Template table for a breakdown section."""
    previous = COMPARISON_LABELS[section["compared_to"]]
    header = [DIMENSION_LABELS[section["dimension"]], "Net revenue", "Share", "Change"]
    if section["show_margin"]:
        header += ["Gross margin", "Margin change"]
    rows = []
    for r in section["rows"]:
        cells = [
            cell(r["value"], "left"),
            cell(fmt_money(r["net_revenue"])),
            cell(fmt_pct(r["share"])),
            change_cell(r["net_revenue_change"], "pct"),
        ]
        if section["show_margin"]:
            cells.append(cell(fmt_pct(r["gross_margin_pct"])))
            cells.append(change_cell(r["gross_margin_change"], "pp"))
        rows.append(cells)
    return {
        "title": section["title"],
        "note": f"Change vs {previous}.",
        "header": header,
        "rows": rows,
    }


def top_table_views(section: dict) -> list[dict]:
    """Template tables for a top-n section and its dropped-out list."""
    previous = COMPARISON_LABELS[section["compared_to"]]
    name_header = "Product" if section["entity"] == "products" else "Customer"
    detail_header = "Category" if section["entity"] == "products" else "Type"
    main = {
        "title": section["title"],
        "note": f"Change vs {previous}; n/a means no sales then.",
        "header": [
            "#",
            name_header,
            detail_header,
            "Net revenue",
            section["volume_label"],
            "Change",
        ],
        "rows": [
            [
                cell(fmt_count(r["rank"])),
                cell(r["name"], "left"),
                cell(r["detail"], "left"),
                cell(fmt_money(r["net_revenue"])),
                cell(fmt_count(r["volume"])),
                change_cell(r["net_revenue_change"], "pct"),
            ]
            for r in section["rows"]
        ],
    }
    views = [main]
    if section["dropped_out"]:
        views.append(
            {
                "title": section["dropped_title"],
                "header": [name_header, "Was #", "Then", "Now", "Now #", "Change"],
                "rows": [
                    [
                        cell(r["name"], "left"),
                        cell(fmt_count(r["previous_rank"])),
                        cell(fmt_money(r["previous_net_revenue"])),
                        cell(fmt_money(r["current_net_revenue"])),
                        cell(fmt_count(r["current_rank"])),
                        change_cell(r["net_revenue_change"], "pct"),
                    ]
                    for r in section["dropped_out"]
                ],
                "minor": True,
            }
        )
    return views


def section_views(section: dict, comparisons: list[str]) -> list[dict]:
    """Turn one section's numbers into template blocks."""
    if section["type"] == "kpis":
        return [kpi_table_view(section, comparisons)]
    if section["type"] == "breakdown":
        return [breakdown_table_view(section)]
    if section["type"] == "top":
        return top_table_views(section)
    if section["type"] == "groups":
        return demand_sections().group_views(section)
    return [{"title": section["title"], "placeholder": section["note"]}]


def render_html(numbers: dict) -> str:
    """Render the email HTML from the report numbers only."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )
    comparisons = list(numbers["comparisons"])
    blocks = []
    for name, section in numbers["sections"].items():
        blocks += section_views(section, comparisons)
        # Charts go straight after the headline KPIs, where the reader looks first.
        if name.startswith("kpis_"):
            blocks += [
                {"chart": cid, "title": chart["title"], "width": CHART_DISPLAY_WIDTH_PX}
                for cid, chart in numbers["charts"].items()
            ]
    return env.get_template(TEMPLATE_NAME).render(
        numbers=numbers, blocks=blocks, summary_placeholder=SUMMARY_PLACEHOLDER
    )


# ---------------------------------------------------------------- Excel


def excel_frames(numbers: dict) -> list[tuple[str, pd.DataFrame, dict[str, str]]]:
    """(sheet name, table, {column: format kind}) for each section that has a sheet."""
    frames = []
    comparisons = list(numbers["comparisons"])
    for name, section in numbers["sections"].items():
        if name.startswith("kpis_"):
            frames.append(("KPIs", *kpi_frame(section, comparisons)))
        elif section["type"] == "breakdown":
            frames.append((EXCEL_SHEETS[section["dimension"]], *breakdown_frame(section)))
        elif section["type"] == "top":
            frames.append((EXCEL_SHEETS[f"top_{section['entity']}"], *top_frame(section)))
        elif section["type"] == "groups":
            frames.append(demand_sections().excel_frame(section))
    return frames


def kpi_frame(section: dict, comparisons: list[str]) -> tuple[pd.DataFrame, dict[str, str]]:
    """KPI sheet. Value formats vary by row, so they are listed per row in 'kind'."""
    records = []
    for row in section["rows"]:
        record = {"KPI": row["label"], "kind": row["kind"], "This period": row["current"]}
        for name in comparisons:
            label = COMPARISON_LABELS[name]
            record[label.capitalize()] = row["comparisons"][name]["value"]
            unit = "pp" if row["kind"] == "rate" else "%"
            record[f"Change vs {label} ({unit})"] = row["comparisons"][name]["change"]
        records.append(record)
    df = pd.DataFrame(records)
    formats = {c: "change" for c in df.columns if c.startswith("Change")}
    return df, formats


def breakdown_frame(section: dict) -> tuple[pd.DataFrame, dict[str, str]]:
    """Breakdown sheet: revenue, share, margin and changes vs the primary comparison."""
    label = COMPARISON_LABELS[section["compared_to"]]
    df = pd.DataFrame(
        {
            DIMENSION_LABELS[section["dimension"]]: [r["value"] for r in section["rows"]],
            "Net revenue": [r["net_revenue"] for r in section["rows"]],
            "Share": [r["share"] for r in section["rows"]],
            f"Net revenue, {label}": [r["previous_net_revenue"] for r in section["rows"]],
            f"Change vs {label} (%)": [r["net_revenue_change"] for r in section["rows"]],
            "Gross margin": [r["gross_margin_pct"] for r in section["rows"]],
            f"Gross margin, {label}": [r["previous_gross_margin_pct"] for r in section["rows"]],
            f"Margin change vs {label} (pp)": [r["gross_margin_change"] for r in section["rows"]],
        }
    )
    formats = {
        "Net revenue": "money",
        f"Net revenue, {label}": "money",
        "Share": "rate",
        "Gross margin": "rate",
        f"Gross margin, {label}": "rate",
        f"Change vs {label} (%)": "change",
        f"Margin change vs {label} (pp)": "change",
    }
    return df, formats


def top_frame(section: dict) -> tuple[pd.DataFrame, dict[str, str]]:
    """Top products or customers sheet."""
    label = COMPARISON_LABELS[section["compared_to"]]
    rows = section["rows"]
    df = pd.DataFrame(
        {
            "Rank": [r["rank"] for r in rows],
            "Product" if section["entity"] == "products" else "Customer": [r["name"] for r in rows],
            "Category" if section["entity"] == "products" else "Type": [r["detail"] for r in rows],
            "Net revenue": [r["net_revenue"] for r in rows],
            section["volume_label"]: [r["volume"] for r in rows],
            f"Net revenue, {label}": [r["previous_net_revenue"] for r in rows],
            f"Change vs {label} (%)": [r["net_revenue_change"] for r in rows],
        }
    )
    formats = {
        "Net revenue": "money",
        f"Net revenue, {label}": "money",
        section["volume_label"]: "count",
        f"Change vs {label} (%)": "change",
    }
    return df, formats


def write_excel(numbers: dict, path: Path) -> None:
    """Write one sheet per table with a frozen header, widths and number formats."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df, formats in excel_frames(numbers):
            out = df.drop(columns="kind") if "kind" in df.columns else df
            out.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            ws.freeze_panes = "A2"
            for col_idx, column in enumerate(out.columns, start=1):
                letter = get_column_letter(col_idx)
                values = [str(column)] + [str(v) for v in out[column]]
                width = min(
                    max(len(v) for v in values) + EXCEL_COLUMN_PADDING, EXCEL_MAX_COLUMN_WIDTH
                )
                ws.column_dimensions[letter].width = width
                for row_idx in range(len(out)):
                    # KPI rows each have their own kind; other sheets format by column.
                    kind = formats.get(column) or (
                        df["kind"].iloc[row_idx] if "kind" in df.columns else None
                    )
                    if kind:
                        ws.cell(row=row_idx + 2, column=col_idx).number_format = EXCEL_FORMATS[kind]


# ---------------------------------------------------------------- build


def build_report(
    report_name: str,
    as_of: date,
    db_path: Path = kpis.DB_PATH,
    output_root: Path = OUTPUT_ROOT,
    config_path: Path = CONFIG_PATH,
    data_end: date | None = None,
    summary: bool = True,
) -> Report:
    """Build one report for the period ending on as_of and save the dry-run files.

    data_end (DATA_END_DATE, set by the scheduler) is only shown in the email, so the
    reader knows the data stops there. summary=False skips the LLM summary (status
    "skipped"). Raises ValueError for an unknown report, a partial period (see
    kpis.period_for) or an as_of after the last order in the database; LLM problems
    never raise.
    """
    # Imported here because summary.py uses this module's formatters.
    from src.reports import summary as report_summary

    config = load_config(config_path)
    if report_name not in config:
        raise ValueError(f"Unknown report {report_name!r}; expected one of {sorted(config)}.")
    block = config[report_name]
    period = kpis.period_for(block["period"], as_of)
    comparisons = {name: kpis.comparison_period(period, name) for name in block["compare_to"]}

    con = kpis.open_db(db_path)
    try:
        last = kpis.last_order_date(con)
        if as_of > last:
            raise ValueError(f"as_of {as_of} is after the last order date {last}.")
        ctx = Context(con, period, comparisons, as_of=as_of, db_path=db_path)
        sections = {query: QUERY_SECTIONS[query](ctx) for query in block["queries"]}
        images, charts = {}, {}
        for chart in block.get("charts", []):
            images[chart], charts[chart] = CHARTS[chart](ctx, sections)
    finally:
        con.close()

    title = block["title"]
    subject = f"{title}: {period.label}"
    numbers = {
        "report": report_name,
        "title": title,
        "subject": subject,
        "as_of": as_of.isoformat(),
        "data_end": data_end.isoformat() if data_end else None,
        "period": period_info(period),
        "comparisons": {
            name: {**period_info(p), "name": COMPARISON_LABELS[name]}
            for name, p in comparisons.items()
        },
        "summary": None,  # filled below by the LLM, from the numbers above only
        "summary_status": None,
        "sections": sections,
        "charts": charts,
    }
    outcome = report_summary.write_summary(numbers) if summary else report_summary.skipped()
    numbers["summary"], numbers["summary_status"] = outcome.text, outcome.status
    html = render_html(numbers)

    output_dir = output_root / f"{report_name}_{as_of.isoformat()}"
    excel_path = output_dir / f"{report_name}_{as_of.isoformat()}.xlsx"
    save_dry_run(html, images, output_dir)
    if block.get("attach_excel"):
        write_excel(numbers, excel_path)
    else:
        excel_path = None
    logger.info("Built %s for %s in %s", report_name, as_of, output_dir)
    return Report(
        report_name,
        title,
        as_of,
        subject,
        html,
        images,
        excel_path,
        numbers,
        output_dir,
        summary_status=outcome.status,
        summary_cost_usd=outcome.cost_usd,
    )


def save_dry_run(html: str, images: dict[str, bytes], output_dir: Path) -> None:
    """Save the HTML and chart PNGs; the saved HTML points at the PNG files, not cid:."""
    output_dir.mkdir(parents=True, exist_ok=True)
    browser_html = html
    for cid, png in images.items():
        (output_dir / f"{cid}.png").write_bytes(png)
        browser_html = browser_html.replace(f'src="cid:{cid}"', f'src="{cid}.png"')
    (output_dir / HTML_FILENAME).write_text(browser_html, encoding="utf-8")


# ---------------------------------------------------------------- command line


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Build reports as dry-run files.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--report", help="report name from config/reports.yaml")
    target.add_argument("--all", action="store_true", help="build every report that fits as_of")
    parser.add_argument(
        "--as-of", required=True, type=date.fromisoformat, help="last day of the period, YYYY-MM-DD"
    )
    parser.add_argument("--db", type=Path, default=kpis.DB_PATH, help="DuckDB file to read")
    parser.add_argument(
        "--no-summary", action="store_true", help="skip the LLM summary (no API call)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Build the requested reports and print where the files went."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    load_dotenv()  # OPENROUTER_API_KEY and LLM_* for the summary
    names = list(load_config()) if args.all else [args.report]
    for name in names:
        try:
            report = build_report(name, args.as_of, db_path=args.db, summary=not args.no_summary)
        except ValueError as error:
            if not args.all:
                raise
            print(f"skipped {name}: {error}")
            continue
        print(f"{name}: {report.output_dir / HTML_FILENAME} (summary: {report.summary_status})")
        if report.excel_path:
            print(f"{' ' * len(name)}  {report.excel_path}")


if __name__ == "__main__":
    main()
