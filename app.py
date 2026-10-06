"""RetailPulse AI: decision-focused Streamlit dashboard over Phase 2 outputs."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "processed"
NAV = [
    "Executive Overview",
    "Customer Intelligence",
    "Product & Market Intelligence",
    "Risks & Opportunities",
]
PARQUET_FILES = [
    "time_series_daily", "time_series_monthly", "customer_rfm", "customer_segments",
    "clustering_evaluation", "cluster_profiles", "product_metrics", "product_monthly_metrics",
    "country_metrics", "country_monthly_metrics", "return_product_metrics",
    "return_country_metrics", "customer_risk", "opportunities", "recommendations", "order_metrics",
]
JSON_FILES = ["kpi_summary", "cancellation_return_summary", "validation_report"]
REQUIRED_COLUMNS = {
    "time_series_daily": {"day", "sales_revenue", "sales_orders"},
    "customer_rfm": {"customer_key", "recency", "frequency_orders", "monetary_value", "cluster_id"},
    "customer_risk": {"customer_key", "segment", "risk_level", "risk_score", "risk_priority", "customer_value", "recency", "frequency_orders", "recommended_action", "is_high_value_at_risk"},
    "cluster_profiles": {"segment", "customers", "revenue", "revenue_share", "avg_monetary", "avg_frequency", "avg_recency_days"},
    "product_metrics": {"stock_code", "product_description", "product_classification", "sales_revenue", "sales_units", "sales_orders", "sales_customers", "revenue_share", "returns_revenue", "return_line_rate", "revenue_growth", "orders_2010_jan_nov", "orders_2011_jan_nov", "unusual_volume_flag"},
    "product_monthly_metrics": {"stock_code", "month", "sales_revenue"},
    "country_metrics": {"country", "sales_revenue", "revenue_share", "sales_orders_global", "sales_customers", "aov", "revenue_growth", "orders_2010_jan_nov", "orders_2011_jan_nov"},
    "country_monthly_metrics": {"country", "month", "sales_revenue"},
    "return_country_metrics": {"country", "returns_revenue"},
    "return_product_metrics": {"stock_code", "returns_revenue", "returns_units", "returns_lines"},
    "opportunities": {"category", "evidence", "business_meaning", "recommended_action", "priority"},
    "recommendations": {"segment", "observation", "insight", "business_impact", "recommended_action", "priority"},
}
COLORS = {
    "navy": "#142b45", "blue": "#2463a6", "teal": "#168b83", "green": "#27845b",
    "amber": "#c47a12", "red": "#bd4a4a", "muted": "#66788a", "pale": "#f3f6f9",
}
SEGMENT_COLORS = {
    "Low-Value Inactive": "#8795a5", "Recent Developing": "#318a82",
    "Valuable At Risk": "#c47a12", "High-Value Active": "#2868a3",
}


st.set_page_config(page_title="RetailPulse AI", page_icon="◉", layout="wide", initial_sidebar_state="expanded")


@st.cache_data(show_spinner="Loading validated analytical outputs…")
def load_parquet(name: str) -> pd.DataFrame:
    path = DATA / f"{name}.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"Required processed file is missing: {path.relative_to(ROOT)}")
    return pd.read_parquet(path)


@st.cache_data(show_spinner=False)
def load_json(name: str) -> dict:
    path = DATA / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Required processed file is missing: {path.relative_to(ROOT)}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def fmt_gbp(value: object, decimals: int = 0) -> str:
    try:
        number = float(value)
        if not pd.notna(number):
            return chr(0x2014)
        sign = "-" if number < 0 else ""
        number = abs(number)
        symbol = chr(0x00A3)
        if number >= 1_000_000:
            return f"{sign}{symbol}{number / 1_000_000:,.2f}M"
        if number >= 10_000:
            return f"{sign}{symbol}{number / 1_000:,.1f}K"
        return f"{sign}{symbol}{number:,.{decimals}f}"
    except (TypeError, ValueError):
        return chr(0x2014)

def fmt_num(value: object, decimals: int = 0) -> str:
    try:
        number = float(value)
        return f"{number:,.{decimals}f}" if pd.notna(number) else "—"
    except (TypeError, ValueError):
        return "—"


def fmt_pct(value: object, decimals: int = 1, signed: bool = False) -> str:
    try:
        number = float(value)
        if not pd.notna(number):
            return "—"
        sign = "+" if signed and number > 0 else ""
        return f"{sign}{number:.{decimals}%}"
    except (TypeError, ValueError):
        return "—"


def nice_product(row: pd.Series) -> str:
    description = str(row.get("product_description", "")).strip()
    code = str(row.get("stock_code", "")).strip()
    if not description or description.lower() in {"nan", "<na>", "none"}:
        return f"{code} · Description unavailable"
    return f"{description.title()} · {code}"


def heading(title: str, subtitle: str | None = None) -> None:
    st.markdown(f'<div class="eyebrow">RETAILPULSE AI <span> / </span> {title.upper()}</div>', unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.caption(subtitle)


def insight_card(label: str, fact: str, meaning: str, action: str, tone: str = "blue") -> None:
    st.markdown(
        f'''<div class="insight-card {tone}">
        <div class="insight-label">{label}</div>
        <div class="insight-fact">{fact}</div>
        <div class="insight-row"><b>Interpretation</b><span>{meaning}</span></div>
        <div class="insight-row"><b>Business implication</b><span>{action}</span></div>
        </div>''', unsafe_allow_html=True,
    )


def metric_row(items: list[tuple[str, str, str | None]]) -> None:
    cols = st.columns(len(items))
    for col, (label, value, help_text) in zip(cols, items):
        col.metric(label, value, help=help_text)


def empty_state(message: str = "No rows match these filters. Adjust the selections to see results.") -> None:
    st.info(message)


def currency_figure(fig: go.Figure, height: int = 360) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=12, r=12, t=54, b=12),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, Segoe UI, sans-serif", color=COLORS["navy"], size=12),
        title_font=dict(size=16, color=COLORS["navy"]),
        hoverlabel=dict(bgcolor="white", font_size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(showgrid=False, linecolor="#dce3ea", tickfont=dict(color=COLORS["muted"]))
    fig.update_yaxes(gridcolor="#e8edf2", zeroline=False, tickfont=dict(color=COLORS["muted"]))
    return fig


def table_view(frame: pd.DataFrame, *, currency_cols: tuple[str, ...] = (), percent_cols: tuple[str, ...] = (), int_cols: tuple[str, ...] = (), rename: dict[str, str] | None = None, height: int = 360) -> None:
    if frame.empty:
        empty_state()
        return
    view = frame.copy()
    for col in currency_cols:
        if col in view:
            view[col] = view[col].map(fmt_gbp)
    for col in percent_cols:
        if col in view:
            view[col] = view[col].map(fmt_pct)
    for col in int_cols:
        if col in view:
            view[col] = view[col].map(lambda x: fmt_num(x))
    if rename:
        view = view.rename(columns=rename)
    st.dataframe(view, width="stretch", hide_index=True, height=height)


def date_filtered(frame: pd.DataFrame, date_col: str, date_range: tuple) -> pd.DataFrame:
    if frame.empty or date_col not in frame:
        return frame.iloc[0:0].copy() if date_col not in frame else frame.copy()
    out = frame.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
    start, end = date_range[0], date_range[1]
    dates = out[date_col].dt.date
    return out.loc[dates.between(start, end)]


def month_filtered(frame: pd.DataFrame, month_col: str, date_range: tuple) -> pd.DataFrame:
    """Keep monthly buckets intersecting the selected start/end calendar months."""
    if frame.empty or month_col not in frame:
        return frame.iloc[0:0].copy() if month_col not in frame else frame.copy()
    months = pd.to_datetime(frame[month_col].astype(str) + "-01", errors="coerce").dt.to_period("M")
    start = pd.Period(date_range[0], freq="M")
    end = pd.Period(date_range[1], freq="M")
    return frame.loc[months.between(start, end)].copy()


@st.cache_data(show_spinner=False)
def make_product_options(products: pd.DataFrame) -> list[tuple[str, str]]:
    ordered = products.sort_values("sales_revenue", ascending=False)
    return [("All products", "All products")] + [(nice_product(row), str(row.stock_code)) for _, row in ordered.iterrows()]


def load_all() -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
    tables = {name: load_parquet(name) for name in PARQUET_FILES}
    summaries = {name: load_json(name) for name in JSON_FILES}
    for name, required in REQUIRED_COLUMNS.items():
        missing = required.difference(tables[name].columns)
        if missing:
            raise ValueError(f"Processed file {name}.parquet is missing required columns: {', '.join(sorted(missing))}")
    return tables, summaries


def show_methodology(kpi: dict, cancel: dict, validation: dict) -> None:
    with st.expander("Methodology, definitions & data limitations"):
        st.markdown("**Source.** UCI Online Retail II, supplied workbook; two overlapping sheets combined by actual invoice date. The dashboard reads only compact files in `data/processed/` and never opens the Excel workbook.")
        st.markdown("**Cleaning.** Exact full-row repeats were removed from analytical aggregates; 34,335 duplicate occurrences were excluded, including 33,663 eligible merchandise lines. They reduced gross merchandise sales by £466,309.43 across 5,079 invoices; no eligible invoice was lost. The source workbook remains unchanged.")
        st.markdown("**Sales and returns.** Gross merchandise sales include eligible numeric-invoice, non-C, positive-quantity, positive-price merchandise lines. Defined returns require a C-prefixed invoice, negative quantity, positive price and merchandise classification. Their signed value is reported separately; net merchandise sales equal gross sales plus that signed return value. Services, vouchers, accounting adjustments and zero-price lines are separate.")
        st.markdown("**Customer coverage.** Customer analysis uses identified customers only. 13.11% of eligible merchandise revenue has no Customer ID, so customer-level findings do not cover all sales.")
        st.markdown("**RFM and segments.** Recency is days since the last eligible purchase (reference date 2011-12-10); frequency is distinct eligible orders; monetary is eligible gross merchandise value. Log-transformed RFM was standardized before K-Means. K=4 was selected after comparing K=2–6 for silhouette, profile interpretability and stability.")
        st.markdown("**Risk.** Rule-based Customer Risk Indicator = 50% recency percentile + 30% six-month spending decline + 20% defined return-value ratio. Labels: Low ≤50, Moderate >50–75, High >75. The high-value at-risk opportunity uses top-quartile monetary value and score ≥60. This is not a churn prediction model.")
        st.markdown("**Comparisons and limitations.** Revenue growth compares Jan–Nov 2011 with Jan–Nov 2010; December is omitted because the 2011 sheet ends on December 9. Historical growth does not establish causation or predict future demand. Unprefixed negative quantities are unpriced signals and are not included in monetary returns.")
        with st.container():
            st.caption(f"Source rows: {fmt_num(validation.get('rows_raw'))} · after analytical deduplication: {fmt_num(validation.get('rows_after_exact_dedup'))} · GBP values are gross unless explicitly labelled net or return.")
        st.caption(f"Return signals: {fmt_num(cancel.get('negative_quantity_without_c_prefix_lines_before_dedup'))} unprefixed negative-quantity rows had zero price and remain outside monetary return totals.")
        with st.expander("KPI formulas"):
            st.json(kpi.get("formulas", {}))


def executive_page(t: dict[str, pd.DataFrame], k: dict, c: dict, v: dict, date_range: tuple, country_filter: list[str], product_code: str) -> None:
    heading("RetailPulse AI", "Retail Revenue & Customer Intelligence · evidence for better commercial decisions")
    st.markdown("A decision-support view of merchandise performance, customer value, returns and growth opportunities. Headline metrics retain the validated full-period Phase 2 definitions.")
    st.markdown('<div class="section-kicker">BUSINESS PERFORMANCE · FULL VALIDATED PERIOD</div>', unsafe_allow_html=True)
    metric_row([
        ("Gross sales", fmt_gbp(k["gross_positive_sales_value"]), "Eligible gross merchandise sales, before defined returns."),
        ("Net sales", fmt_gbp(k["net_sales_after_defined_returns"]), "Gross merchandise sales plus signed defined returns."),
        ("Orders", fmt_num(k["orders"]), "Distinct eligible merchandise invoices."),
        ("Identified customers", fmt_num(k["customers"]), "Distinct customers with ID on eligible sales."),
        ("Average order value", fmt_gbp(k["average_order_value"], 2), "Gross eligible sales divided by eligible orders."),
        ("Comparable growth", fmt_pct(k["revenue_growth_2011_jan_nov_vs_2010_jan_nov"], 2, True), "Jan–Nov 2011 versus Jan–Nov 2010."),
    ])
    st.caption(f"Paid units {fmt_num(k['units'])} · Repeat-customer rate {fmt_pct(k['repeat_customer_rate'])} · Return line rate {fmt_pct(k['c_invoice_return_line_rate'])} · Validated KPI cards are not recalculated by chart filters.")

    ts = t["time_series_daily"].copy()
    ts["date"] = pd.to_datetime(ts["day"], errors="coerce")
    ts = date_filtered(ts, "date", date_range)
    if not ts.empty:
        ts["month_date"] = ts["date"].dt.to_period("M").dt.to_timestamp()
        ts = ts.groupby("month_date", as_index=False)[["sales_revenue", "sales_units", "sales_lines", "sales_orders"]].sum()
        ts = ts.rename(columns={"month_date": "date"})
    start, end = st.columns(2)
    start.write(f"**Chart period:** {date_range[0].strftime('%d %b %Y')} – {date_range[1].strftime('%d %b %Y')}")
    end.caption("Monthly aggregation · merchandise only · return values are shown separately in the risk section.")
    if ts.empty:
        empty_state("No monthly data falls within this date range. Widen the date filter in the sidebar.")
    else:
        left, right = st.columns(2)
        fig = px.area(ts, x="date", y="sales_revenue", title="Merchandise revenue by month", labels={"date": "Invoice month", "sales_revenue": "Gross sales (GBP)"}, color_discrete_sequence=[COLORS["blue"]], custom_data=["sales_orders"])
        fig.update_traces(hovertemplate="%{x|%b %Y}<br>Gross sales: £%{y:,.2f}<br>Orders: %{customdata[0]:,.0f}<extra></extra>")
        fig.update_yaxes(tickprefix="£", tickformat="~s")
        left.plotly_chart(currency_figure(fig, 330), width="stretch", config={"displayModeBar": False})
        fig = px.line(ts, x="date", y="sales_orders", title="Eligible orders by month", labels={"date": "Invoice month", "sales_orders": "Orders"}, markers=True, color_discrete_sequence=[COLORS["teal"]])
        fig.update_traces(hovertemplate="%{x|%b %Y}<br>Eligible orders: %{y:,.0f}<extra></extra>")
        right.plotly_chart(currency_figure(fig, 330), width="stretch", config={"displayModeBar": False})

    st.markdown('<div class="section-kicker">WHERE PERFORMANCE COMES FROM</div>', unsafe_allow_html=True)
    p, m = st.columns([1.1, 0.9])
    products = t["product_metrics"].copy()
    if product_code != "All products":
        products = products.loc[products.stock_code.astype(str).eq(product_code)]
    top = products.nlargest(8, "sales_revenue").copy()
    if top.empty:
        with p: empty_state("No products match the selected product.")
    else:
        top["label"] = top.apply(nice_product, axis=1)
        fig = px.bar(top.sort_values("sales_revenue"), x="sales_revenue", y="label", orientation="h", title="Leading products by gross merchandise sales", labels={"sales_revenue": "Gross sales (GBP)", "label": "Product"}, color_discrete_sequence=[COLORS["blue"]], custom_data=["sales_orders", "revenue_share"])
        fig.update_traces(hovertemplate="%{y}<br>Gross sales: £%{x:,.2f}<br>Orders: %{customdata[0]:,.0f}<br>Sales contribution: %{customdata[1]:.1%}<extra></extra>")
        fig.update_xaxes(tickprefix="£", tickformat="~s")
        p.plotly_chart(currency_figure(fig, 370), width="stretch", config={"displayModeBar": False})
        with st.expander('Product detail - supporting metrics'):
            table_view(top[["label", "sales_revenue", "sales_orders", "revenue_share"]].head(6), currency_cols=("sales_revenue",), percent_cols=("revenue_share",), int_cols=("sales_orders",), rename={"label": "Product", "sales_revenue": "Gross sales", "sales_orders": "Orders", "revenue_share": "Revenue share"}, height=250)
    countries = t["country_metrics"].copy()
    if country_filter:
        countries = countries.loc[countries.country.isin(country_filter)]
    top_c = countries.nlargest(8, "sales_revenue").sort_values("sales_revenue")
    if top_c.empty:
        with m: empty_state("No markets match the selected countries.")
    else:
        fig = px.bar(top_c, x="sales_revenue", y="country", orientation="h", title="Market revenue concentration", labels={"sales_revenue": "Gross sales (GBP)", "country": "Market"}, color="revenue_share", color_continuous_scale=["#bad7d4", COLORS["blue"]], custom_data=["revenue_share", "sales_orders_global"])
        fig.update_traces(hovertemplate="%{y}<br>Gross sales: £%{x:,.2f}<br>Business revenue share: %{customdata[0]:.1%}<br>Orders: %{customdata[1]:,.0f}<extra></extra>")
        fig.update_xaxes(tickprefix="£", tickformat="~s")
        m.plotly_chart(currency_figure(fig, 370), width="stretch", config={"displayModeBar": False})
        with st.expander('Market detail - supporting metrics'):
            table_view(top_c.sort_values("sales_revenue", ascending=False)[["country", "sales_revenue", "sales_orders_global", "revenue_share"]].head(6), currency_cols=("sales_revenue",), percent_cols=("revenue_share",), int_cols=("sales_orders_global",), rename={"country": "Market", "sales_revenue": "Gross sales", "sales_orders_global": "Orders", "revenue_share": "Business revenue share"}, height=250)

    st.markdown('<div class="section-kicker">WHAT THE DATA SAYS</div>', unsafe_allow_html=True)
    profiles = t["cluster_profiles"].sort_values("revenue_share", ascending=False)
    high_value = profiles.loc[profiles.segment.eq("High-Value Active")]
    uk = t["country_metrics"].loc[t["country_metrics"].country.eq("United Kingdom")]
    uk_share = float(uk.revenue_share.iloc[0]) if not uk.empty else 0.0
    top_prod = t["product_metrics"].nlargest(1, "sales_revenue")
    opportunities = t["opportunities"]
    growth = float(k["revenue_growth_2011_jan_nov_vs_2010_jan_nov"])
    insights = st.columns(2)
    with insights[0]:
        insight_card("Comparable growth", f"{fmt_pct(growth, 2, True)} across Jan–Nov", "Eligible sales rose modestly versus the same 11-month window a year earlier.", "Treat as measured growth; test drivers by product and market before broad expansion.", "blue")
    with insights[1]:
        insight_card("Market mix", f"UK contributes {fmt_pct(uk_share)} of gross merchandise sales", "Sales are strongly concentrated in the largest market.", "Protect the core market while assessing evidence-backed secondary-market tests.", "amber")
    insights = st.columns(2)
    if not high_value.empty:
        r = high_value.iloc[0]
        with insights[0]: insight_card("Customer value", f"{fmt_num(r.customers)} High-Value Active customers generate {fmt_pct(r.revenue_share)} of identified sales", "A relatively small segment accounts for most identified customer revenue.", "Protect service and repeat purchasing for this segment.", "green")
    if not top_prod.empty:
        r = top_prod.iloc[0]
        with insights[1]: insight_card("Leading product", f"{nice_product(r)} · {fmt_gbp(r.sales_revenue)}", f"It ranks first by eligible gross merchandise revenue ({fmt_num(r.sales_orders)} orders).", "Monitor availability and return signals alongside its sales contribution.", "blue")
    if opportunities.empty:
        st.caption("No additional qualifying opportunity records were produced by the Phase 2 rules.")
    show_methodology(k, c, v)


def customer_page(t: dict[str, pd.DataFrame], k: dict, segments_filter: list[str]) -> None:
    heading("Customer Intelligence", "Understand customer value, purchase behavior and transparent risk signals")
    profiles = t["cluster_profiles"].copy()
    risk = t["customer_risk"].copy()
    if segments_filter:
        profiles = profiles.loc[profiles.segment.isin(segments_filter)]
        risk = risk.loc[risk.segment.isin(segments_filter)]
    total_customers = int(profiles.customers.sum()) if not profiles.empty else 0
    repeat_count = float(k["repeat_customer_rate"])
    segment_count = profiles.set_index("segment").customers.to_dict() if not profiles.empty else {}
    active_count = int(segment_count.get("High-Value Active", 0))
    at_risk_count = int(risk.is_high_value_at_risk.sum()) if not risk.empty else 0
    metric_row([
        ("Identified customers", fmt_num(total_customers), "Selected segments; full portfolio is 5,852."),
        ("Repeat customer rate", fmt_pct(repeat_count), "Validated portfolio rate; not changed by segment filter."),
        ("Revenue per identified customer", fmt_gbp(k["revenue_per_identified_customer"], 2), "Validated full-portfolio average."),
        ("High-Value Active", fmt_num(active_count), "Count in selected segments."),
        ("Valuable at-risk priority", fmt_num(at_risk_count), "Top-quartile value and risk score ≥60 in selected segments."),
    ])
    st.markdown('<div class="section-kicker">RFM SEGMENTATION · DESCRIPTIVE, NOT PREDICTIVE</div>', unsafe_allow_html=True)
    st.markdown("**R**ecency measures days since last purchase · **F**requency counts eligible orders · **M**onetary is eligible gross merchandise sales. K-Means groups similar historical RFM patterns; it does not predict churn.")
    if profiles.empty:
        empty_state("No customer segments are selected.")
        return
    left, right = st.columns([1.05, 0.95])
    fig = px.bar(profiles.sort_values("customers", ascending=True), x="customers", y="segment", orientation="h", title="Customer count by validated segment", labels={"customers": "Customers", "segment": "Segment"}, color="segment", color_discrete_map=SEGMENT_COLORS, custom_data=["revenue_share", "avg_monetary", "avg_frequency", "avg_recency_days"])
    fig.update_traces(hovertemplate="%{y}<br>Customers: %{x:,.0f}<br>Revenue share: %{customdata[0]:.1%}<br>Average monetary: £%{customdata[1]:,.2f}<br>Average orders: %{customdata[2]:.2f}<br>Average recency: %{customdata[3]:.1f} days<extra></extra>")
    left.plotly_chart(currency_figure(fig, 360), width="stretch", config={"displayModeBar": False})
    fig = px.pie(profiles, values="revenue", names="segment", hole=0.62, title="Identified customer revenue mix", color="segment", color_discrete_map=SEGMENT_COLORS, custom_data=["customers", "revenue_share"])
    fig.update_traces(textinfo="percent", hovertemplate="%{label}<br>Revenue: £%{value:,.2f}<br>Share: %{customdata[1]:.1%}<br>Customers: %{customdata[0]:,.0f}<extra></extra>")
    right.plotly_chart(currency_figure(fig, 360), width="stretch", config={"displayModeBar": False})
    with st.expander('Segment metrics - customer count, value, frequency and recency'):
        table_view(profiles[["segment", "customers", "revenue_share", "avg_monetary", "avg_frequency", "avg_recency_days"]], currency_cols=("avg_monetary",), percent_cols=("revenue_share",), int_cols=("customers",), rename={"segment": "Segment", "customers": "Customers", "revenue_share": "Revenue share", "avg_monetary": "Avg. monetary value", "avg_frequency": "Avg. orders", "avg_recency_days": "Avg. recency (days)"}, height=240)

    st.markdown('<div class="section-kicker">SEGMENT MEANING & ACTION</div>', unsafe_allow_html=True)
    actions = {
        "Low-Value Inactive": ("Low average value, about one order, and long average recency.", "Small historical contribution; broad high-cost outreach is not supported by this profile.", "Use low-cost discovery or reactivation tests and measure second-order conversion."),
        "Recent Developing": ("Recent purchasers with moderate order frequency and value.", "Recent activity offers an opportunity to build repeat behavior.", "Test onboarding and relevant follow-up offers; track repeat purchase."),
        "Valuable At Risk": ("Above-average value and frequency with substantially longer recency.", "A meaningful share of identified sales comes from customers who have not purchased recently.", "Prioritize targeted service and win-back tests; compare recovery against a holdout."),
        "High-Value Active": ("Highest monetary value and frequency, with recent purchases.", "This segment supplies most identified customer revenue.", "Protect availability and service quality; test relevant retention actions."),
    }
    cols = st.columns(2)
    for idx, row in enumerate(profiles.itertuples()):
        profile, why, action = actions.get(row.segment, ("Profile details are available in the segment metrics.", "Review alongside its size and revenue share.", "Use the validated Phase 2 segment recommendation."))
        with cols[idx % 2]:
            tone = "amber priority" if row.segment == "Valuable At Risk" else "blue"
            fact = f"{fmt_num(row.customers)} customers \u00b7 {fmt_gbp(row.avg_monetary, 2)} avg monetary \u00b7 {fmt_num(row.avg_recency_days, 0)} days avg recency \u00b7 {fmt_pct(row.revenue_share)} of revenue \u00b7 {fmt_num(row.avg_frequency, 1)} avg orders"
            insight_card(row.segment, fact, profile, f"{why} {action}", tone)

    st.markdown('<div class="section-kicker">CUSTOMER RISK INDICATOR</div>', unsafe_allow_html=True)
    st.info("This is a rule-based risk indicator, not a churn prediction.")
    risk_counts = risk.risk_level.value_counts().reindex(["High", "Moderate", "Low"], fill_value=0).rename_axis("Risk level").reset_index(name="Customers")
    fig = px.bar(risk_counts, x="Risk level", y="Customers", color="Risk level", color_discrete_map={"High": COLORS["red"], "Moderate": COLORS["amber"], "Low": COLORS["teal"]}, title="Customer risk levels", text="Customers")
    fig.update_traces(texttemplate="%{text:,.0f}", hovertemplate="%{x}<br>Customers: %{y:,.0f}<extra></extra>")
    st.plotly_chart(currency_figure(fig, 300), width="stretch", config={"displayModeBar": False})
    with st.expander("How the risk score is calculated"):
        st.markdown("**Score = 50% recency percentile + 30% six-month spending decline + 20% defined return-value ratio.** Recent spend uses Jun–Nov 2011; comparison spend uses Dec 2010–May 2011. Low ≤50; Moderate >50–75; High >75. The high-value at-risk priority additionally requires top-quartile monetary value and score ≥60.")

    st.markdown('<div class="section-kicker">PRIORITIZED HIGH-VALUE CUSTOMERS</div>', unsafe_allow_html=True)
    priority = risk.loc[risk.is_high_value_at_risk].sort_values(["risk_priority", "customer_value"], ascending=False).head(100)
    if priority.empty:
        empty_state("No high-value at-risk customers match the selected segments.")
    else:
        with st.expander('Prioritized customer table - top 100'):
            table_view(priority[["customer_key", "segment", "risk_level", "risk_score", "customer_value", "recency", "frequency_orders", "recommended_action"]], currency_cols=("customer_value",), int_cols=("recency", "frequency_orders"), rename={"customer_key": "Anonymous key", "segment": "Segment", "risk_level": "Risk", "risk_score": "Risk score", "customer_value": "Historical value", "recency": "Recency (days)", "frequency_orders": "Orders", "recommended_action": "Suggested action"}, height=420)
        st.caption("Top 100 shown by Phase 2 risk priority. Anonymous customer keys are analytical labels, not personal identifiers or predictions.")


def product_market_page(t: dict[str, pd.DataFrame], date_range: tuple, country_filter: list[str], product_code: str) -> None:
    heading("Product & Market Intelligence", "Compare product contribution, demand trends, market scale and comparable growth")
    top_n = st.slider("Products and markets to show", min_value=5, max_value=20, value=10, step=1)
    product_tab, market_tab = st.tabs(["Product Intelligence", "Market Intelligence"])
    with product_tab:
        products = t["product_metrics"].copy()
        products = products.loc[products.product_classification.eq("Merchandise")]
        metric_choice = st.radio("Rank products by", ["Gross revenue", "Paid units"], horizontal=True, key="product_rank")
        metric_col = "sales_revenue" if metric_choice == "Gross revenue" else "sales_units"
        selected = products if product_code == "All products" else products.loc[products.stock_code.astype(str).eq(product_code)]
        ranked = selected.nlargest(top_n, metric_col).copy()
        if ranked.empty:
            empty_state("No merchandise products match the selected product.")
        else:
            ranked["label"] = ranked.apply(nice_product, axis=1)
            fig = px.bar(ranked.sort_values(metric_col), x=metric_col, y="label", orientation="h", title=f"Top {len(ranked)} products by {metric_choice.lower()}", labels={metric_col: "Gross sales (GBP)" if metric_col == "sales_revenue" else "Paid units", "label": "Product"}, color_discrete_sequence=[COLORS["blue"]], custom_data=["sales_orders", "revenue_share", "returns_revenue", "return_line_rate"])
            if metric_col == "sales_revenue":
                fig.update_traces(hovertemplate="%{y}<br>Gross sales: £%{x:,.2f}<br>Orders: %{customdata[0]:,.0f}<br>Contribution: %{customdata[1]:.1%}<br>Defined returns: £%{customdata[2]:,.2f}<br>Return line rate: %{customdata[3]:.2%}<extra></extra>")
                fig.update_xaxes(tickprefix="£", tickformat="~s")
            else:
                fig.update_traces(hovertemplate="%{y}<br>Paid units: %{x:,.0f}<br>Orders: %{customdata[0]:,.0f}<br>Revenue contribution: %{customdata[1]:.1%}<br>Signed return value: £%{customdata[2]:,.2f}<extra></extra>")
            st.plotly_chart(currency_figure(fig, max(360, 34 * len(ranked))), width="stretch", config={"displayModeBar": False})
            cols = ["label", "sales_revenue", "sales_units", "sales_orders", "sales_customers", "revenue_share", "returns_revenue", "return_line_rate", "revenue_growth"]
            table_view(ranked[cols], currency_cols=("sales_revenue", "returns_revenue"), percent_cols=("revenue_share", "return_line_rate", "revenue_growth"), int_cols=("sales_units", "sales_orders", "sales_customers"), rename={"label": "Product", "sales_revenue": "Gross sales", "sales_units": "Paid units", "sales_orders": "Orders", "sales_customers": "Identified customers", "revenue_share": "Contribution", "returns_revenue": "Defined returns", "return_line_rate": "Return line rate", "revenue_growth": "Jan–Nov growth"}, height=360)
        st.markdown("**Comparable growth opportunities**")
        growth = products.loc[products.revenue_growth.ge(.20) & products.orders_2010_jan_nov.ge(20) & products.orders_2011_jan_nov.ge(20)].nlargest(top_n, "sales_revenue").copy()
        growth["Product"] = growth.apply(nice_product, axis=1)
        if growth.empty:
            empty_state("No products meet the Phase 2 threshold of ≥20% growth and at least 20 orders in both Jan–Nov windows.")
        else:
            growth_view = growth[["Product", "sales_revenue", "revenue_growth", "orders_2010_jan_nov", "orders_2011_jan_nov", "return_line_rate"]]
            table_view(growth_view.head(5), currency_cols=("sales_revenue",), percent_cols=("revenue_growth", "return_line_rate"), int_cols=("orders_2010_jan_nov", "orders_2011_jan_nov"), rename={"sales_revenue": "All-period gross sales", "revenue_growth": "Jan–Nov growth", "orders_2010_jan_nov": "Orders 2010", "orders_2011_jan_nov": "Orders 2011", "return_line_rate": "Return line rate"}, height=320)
            if len(growth_view) > 5:
                with st.expander("More qualifying product growth detail"):
                    table_view(growth_view.iloc[5:], currency_cols=("sales_revenue",), percent_cols=("revenue_growth", "return_line_rate"), int_cols=("orders_2010_jan_nov", "orders_2011_jan_nov"), rename={"sales_revenue": "All-period gross sales", "revenue_growth": "Jan–Nov growth", "orders_2010_jan_nov": "Orders 2010", "orders_2011_jan_nov": "Orders 2011", "return_line_rate": "Return line rate"}, height=320)
        st.markdown("**Product trend**")
        pmonth = t["product_monthly_metrics"].copy()
        if product_code != "All products":
            pmonth = pmonth.loc[pmonth.stock_code.astype(str).eq(product_code)]
        pmonth = month_filtered(pmonth, "month", date_range)
        pmonth["date"] = pd.to_datetime(pmonth["month"] + "-01", errors="coerce")
        if pmonth.empty:
            empty_state("No product trend rows match this product and date range.")
        else:
            pmonth = pmonth.groupby("date", as_index=False)[["sales_revenue", "sales_units", "sales_orders"]].sum()
            fig = px.line(pmonth, x="date", y="sales_revenue", title="Monthly product revenue · selected products", labels={"date": "Invoice month", "sales_revenue": "Gross sales (GBP)"}, markers=True, color_discrete_sequence=[COLORS["teal"]])
            fig.update_traces(hovertemplate="%{x|%b %Y}<br>Gross sales: £%{y:,.2f}<extra></extra>")
            fig.update_yaxes(tickprefix="£", tickformat="~s")
            st.plotly_chart(currency_figure(fig, 330), width="stretch", config={"displayModeBar": False})
        special = t["product_metrics"].loc[t["product_metrics"].unusual_volume_flag].copy()
        if not special.empty:
            with st.expander("Products flagged for unusual volume concentration"):
                table_view(special[["stock_code", "product_description", "sales_units", "sales_orders", "returns_units", "return_line_rate"]].head(10), int_cols=("sales_units", "sales_orders"), percent_cols=("return_line_rate",), rename={"stock_code": "Stock code", "product_description": "Product", "sales_units": "Paid units", "sales_orders": "Orders", "returns_units": "Return units", "return_line_rate": "Return line rate"}, height=280)

    with market_tab:
        countries = t["country_metrics"].copy()
        if country_filter:
            countries = countries.loc[countries.country.isin(country_filter)]
        if countries.empty:
            empty_state("No markets match the selected countries.")
        else:
            metric_row([
                ("Markets selected", fmt_num(len(countries)), "Markets represented in the selected view."),
                ("Selected market sales", fmt_gbp(countries.sales_revenue.sum()), "Gross merchandise sales across selected markets."),
                ("UK revenue share", fmt_pct(float(t["country_metrics"].loc[t["country_metrics"].country.eq("United Kingdom"), "revenue_share"].sum())), "Full portfolio share; selection does not redefine the denominator."),
            ])
            chart_data = countries.nlargest(top_n, "sales_revenue").sort_values("sales_revenue")
            fig = px.bar(chart_data, x="sales_revenue", y="country", orientation="h", title="Market scale · eligible gross merchandise sales", labels={"sales_revenue": "Gross sales (GBP)", "country": "Market"}, color="revenue_share", color_continuous_scale=["#c8d9e9", COLORS["blue"]], custom_data=["revenue_share", "sales_orders_global", "sales_customers", "aov"])
            fig.update_traces(hovertemplate="%{y}<br>Gross sales: £%{x:,.2f}<br>Business share: %{customdata[0]:.1%}<br>Orders: %{customdata[1]:,.0f}<br>Identified customers: %{customdata[2]:,.0f}<br>AOV: £%{customdata[3]:,.2f}<extra></extra>")
            fig.update_xaxes(tickprefix="£", tickformat="~s")
            st.plotly_chart(currency_figure(fig, max(360, 34 * len(chart_data))), width="stretch", config={"displayModeBar": False})
            growth_markets = countries.loc[countries.orders_2010_jan_nov.ge(100) & countries.orders_2011_jan_nov.ge(100) & countries.revenue_growth.ge(0.10)].nlargest(5, "revenue_growth")
            st.markdown("**Volume-supported comparable market growth**")
            if growth_markets.empty:
                st.caption("No selected market meets the Phase 2 growth and minimum-order thresholds.")
            else:
                table_view(growth_markets[["country", "revenue_growth", "sales_revenue", "orders_2010_jan_nov", "orders_2011_jan_nov"]], currency_cols=("sales_revenue",), percent_cols=("revenue_growth",), int_cols=("orders_2010_jan_nov", "orders_2011_jan_nov"), rename={"country": "Market", "revenue_growth": "Jan-Nov growth", "sales_revenue": "Full-period gross sales", "orders_2010_jan_nov": "Orders 2010", "orders_2011_jan_nov": "Orders 2011"}, height=220)
            display = countries.copy()
            display["sample_status"] = display.apply(lambda r: "Meets minimum in both periods" if r.orders_2010_jan_nov >= 100 and r.orders_2011_jan_nov >= 100 else "Below growth comparison threshold", axis=1)
            with st.expander('Market detail - growth support and customer metrics'):
                table_view(display.sort_values("sales_revenue", ascending=False)[["country", "sales_revenue", "revenue_share", "sales_orders_global", "sales_customers", "aov", "revenue_growth", "orders_2010_jan_nov", "orders_2011_jan_nov", "sample_status"]], currency_cols=("sales_revenue", "aov"), percent_cols=("revenue_share", "revenue_growth"), int_cols=("sales_orders_global", "sales_customers", "orders_2010_jan_nov", "orders_2011_jan_nov"), rename={"country": "Market", "sales_revenue": "Gross sales", "revenue_share": "Business share", "sales_orders_global": "Orders", "sales_customers": "Identified customers", "aov": "AOV", "revenue_growth": "Jan–Nov growth", "orders_2010_jan_nov": "Orders 2010", "orders_2011_jan_nov": "Orders 2011", "sample_status": "Comparison support"}, height=390)
            st.caption("Growth is comparable only when both windows have at least 100 orders. The UK’s 85.5% share is concentration exposure to monitor, not evidence that the market itself is undesirable.")
        st.markdown("**Monthly market trend**")
        cmonth = t["country_monthly_metrics"].copy()
        if country_filter:
            cmonth = cmonth.loc[cmonth.country.isin(country_filter)]
        cmonth = month_filtered(cmonth, "month", date_range)
        cmonth["date"] = pd.to_datetime(cmonth["month"] + "-01", errors="coerce")
        if cmonth.empty:
            empty_state("No market trend data match the selected markets and date range.")
        else:
            cmonth = cmonth.groupby(["date", "country"], as_index=False).sales_revenue.sum()
            if not country_filter:
                leaders = countries.nlargest(5, "sales_revenue").country.tolist()
                cmonth = cmonth.loc[cmonth.country.isin(leaders)]
            fig = px.line(cmonth, x="date", y="sales_revenue", color="country", title="Monthly gross sales by market", labels={"date": "Invoice month", "sales_revenue": "Gross sales (GBP)", "country": "Market"}, markers=False)
            fig.update_traces(hovertemplate="%{fullData.name}<br>%{x|%b %Y}<br>Gross sales: £%{y:,.2f}<extra></extra>")
            fig.update_yaxes(tickprefix="£", tickformat="~s")
            st.plotly_chart(currency_figure(fig, 370), width="stretch", config={"displayModeBar": False})


def risks_page(t: dict[str, pd.DataFrame], k: dict, c: dict, v: dict, country_filter: list[str], segments_filter: list[str]) -> None:
    heading("Risks & Opportunities", "Translate validated signals into prioritized review and action")
    st.markdown('<div class="section-kicker">RISKS</div>', unsafe_allow_html=True)
    risk = t["customer_risk"].copy()
    if segments_filter:
        risk = risk.loc[risk.segment.isin(segments_filter)]
    risk_counts = risk.risk_level.value_counts()
    uk_share = float(t["country_metrics"].loc[t["country_metrics"].country.eq("United Kingdom"), "revenue_share"].sum())
    metric_row([
        ("High risk", fmt_num(risk_counts.get("High", 0)), "Rule-based score >75."),
        ("Moderate risk", fmt_num(risk_counts.get("Moderate", 0)), "Rule-based score >50 and ≤75."),
        ("Low risk", fmt_num(risk_counts.get("Low", 0)), "Rule-based score ≤50."),
        ("Defined return value", fmt_gbp(k["signed_cancellation_return_value"]), "Signed return amount; not treated as all lost sales."),
        ("UK sales concentration", fmt_pct(uk_share), "Share of full-period eligible merchandise sales."),
    ])
    st.info("This is a rule-based risk indicator, not a churn prediction.")
    left, right = st.columns(2)
    risk_chart = risk.risk_level.value_counts().reindex(["High", "Moderate", "Low"], fill_value=0).rename_axis("Risk level").reset_index(name="Customers")
    fig = px.bar(risk_chart, x="Risk level", y="Customers", color="Risk level", color_discrete_map={"High": COLORS["red"], "Moderate": COLORS["amber"], "Low": COLORS["teal"]}, title="Customer risk profile", text="Customers")
    fig.update_traces(texttemplate="%{text:,.0f}", hovertemplate="%{x}<br>Customers: %{y:,.0f}<extra></extra>")
    left.plotly_chart(currency_figure(fig, 310), width="stretch", config={"displayModeBar": False})
    country_returns = t["return_country_metrics"].copy()
    if country_filter:
        country_returns = country_returns.loc[country_returns.country.isin(country_filter)]
    country_returns = country_returns.nsmallest(8, "returns_revenue").sort_values("returns_revenue")
    if country_returns.empty:
        with right: empty_state("No return-by-country rows match selected markets.")
    else:
        fig = px.bar(country_returns, x="returns_revenue", y="country", orientation="h", title="Largest defined return values by market", labels={"returns_revenue": "Signed return value (GBP)", "country": "Market"}, color_discrete_sequence=[COLORS["amber"]])
        fig.update_traces(hovertemplate="%{y}<br>Signed defined return value: £%{x:,.2f}<extra></extra>")
        fig.update_xaxes(tickprefix="£", tickformat="~s")
        right.plotly_chart(currency_figure(fig, 310), width="stretch", config={"displayModeBar": False})
    product_returns = t["return_product_metrics"].merge(t["product_metrics"][["stock_code", "product_description", "sales_revenue", "return_line_rate"]], on="stock_code", how="left")
    product_returns["product"] = product_returns.apply(nice_product, axis=1)
    st.markdown("**Largest defined merchandise returns by product**")
    with st.expander('Return detail - leading merchandise products'):
        table_view(product_returns.nsmallest(10, "returns_revenue")[["product", "returns_revenue", "returns_units", "returns_lines", "sales_revenue", "return_line_rate"]], currency_cols=("returns_revenue", "sales_revenue"), percent_cols=("return_line_rate",), int_cols=("returns_lines",), rename={"product": "Product", "returns_revenue": "Signed return value", "returns_units": "Returned units", "returns_lines": "Return lines", "sales_revenue": "Gross sales", "return_line_rate": "Return line rate"}, height=320)
    with st.expander("Data limitations and interpretation"):
        st.markdown(f"- **Customer coverage:** {fmt_pct(k['unidentified_customer_revenue_share'])} of eligible merchandise revenue ({fmt_gbp(k['unidentified_customer_revenue'])}) has no Customer ID. Customer findings cover identified customers only.\n- **Duplicate treatment:** exact-row deduplication reduced eligible gross sales by {fmt_gbp(v['duplicate_removed_ordinary_sales_value'])}; see the methodology for its impact.\n- **Historical comparison:** growth describes comparable historical windows and does not establish causation.\n- **Unpriced signals:** {fmt_num(c['negative_quantity_without_c_prefix_lines_before_dedup'])} negative-quantity rows without C invoice prefix had zero price; they are not included in monetary return values.\n- **Scope:** return and concentration signals are useful for review, but do not alone establish a root cause or require a particular commercial action.")

    st.markdown('<div class="section-kicker">OPPORTUNITIES</div>', unsafe_allow_html=True)
    opp = t["opportunities"].copy()
    if opp.empty:
        empty_state("No opportunities met the Phase 2 evidence and volume rules.")
    else:
        priority_order = {"High": 0, "Medium": 1, "Low": 2}
        opp["_sort"] = opp.priority.map(priority_order).fillna(3)
        featured = opp.loc[opp.category.eq("High-value at-risk customers")]
        if not featured.empty:
            row = featured.iloc[0]
            match = re.search(r"([\d,]+) customers.*?eligible historical sales total ([\d,]+(?:\.\d{2})?)", row.evidence)
            fact = row.evidence
            if match:
                customer_count = int(match.group(1).replace(",", ""))
                historical_sales = float(match.group(2).replace(",", ""))
                fact = f"{customer_count:,} high-value at-risk customers \u00b7 {chr(0x00A3)}{historical_sales:,.2f} historical eligible sales"
            insight_card("High-value at-risk opportunity \u00b7 High priority", fact, row.business_meaning, row.recommended_action, "amber priority")
        remaining = opp.loc[~opp.category.eq("High-value at-risk customers")]
        for row in remaining.sort_values(["_sort", "category"]).itertuples():
            tone = "amber" if row.priority == "High" else "green"
            insight_card(f"{row.category} \u00b7 {row.priority} priority", row.evidence, row.business_meaning, row.recommended_action, tone)
    st.markdown('<div class="section-kicker">ACTIONS FOR MANAGEMENT</div>', unsafe_allow_html=True)
    recs = t["recommendations"].copy()
    if segments_filter:
        recs = recs.loc[recs.segment.isin(segments_filter)]
    if recs.empty:
        empty_state("No segment recommendations match the selected segments.")
    else:
        recs = recs.rename(columns={"segment": "Finding", "business_impact": "Business impact", "recommended_action": "Recommended action", "priority": "Priority", "observation": "Evidence", "insight": "Interpretation"})
        show = recs[["Priority", "Finding", "Business impact", "Recommended action", "Evidence", "Interpretation"]]
        st.dataframe(show, width="stretch", hide_index=True, height=360)
    with st.expander("How RetailPulse AI works"):
        st.markdown("**RFM** summarizes customer behavior through recency, frequency and monetary value. **K-Means** groups similar log-transformed and scaled RFM behavior into four descriptive segments. The separate **Customer Risk Indicator** applies documented rules to prioritize review. Segmentation is unsupervised; risk scoring is rule-based; neither claims to predict churn.")


def main() -> None:
    st.markdown("""<style>
    :root { --navy:#142b45; --blue:#2463a6; --line:#e4eaf0; }
    .stApp { background:#f7f9fb; color:#142b45; }
    [data-testid="stHeader"] { background:rgba(247,249,251,.92); }
    [data-testid="stSidebar"] { background:#f0f4f7; border-right:1px solid #e1e8ef; }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3,
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color:#203a52 !important; }
    [data-testid="stSidebar"] [role="radiogroup"] label { color:#203a52 !important; font-weight:600; }
    [data-testid="stSidebar"] [role="radio"][aria-checked="true"] { background:#dce8f3; border-radius:8px; }
    [data-testid="stSidebar"] [data-baseweb="select"] > div { background:#fff; color:#203a52; border-color:#b7c5d2; }
    [data-testid="stSidebar"] [data-baseweb="menu"],
    [data-testid="stSidebar"] [data-baseweb="menu"] * { color:#203a52 !important; }
    [data-testid="stSidebar"] [data-baseweb="tag"] { background:#dce8f3; color:#203a52; }
    [data-testid="stSidebar"] button[kind="secondary"] { background:#fff; border:1px solid #9fb0bf; color:#203a52; font-weight:650; }
    [data-testid="stSidebar"] button[kind="secondary"] p { color:#203a52 !important; }
    [data-testid="stSidebar"] input { color:#203a52; }
    h1,h2,h3 { color:#142b45; letter-spacing:-.025em; }
    h1 { font-size:2.05rem !important; margin-bottom:.15rem !important; }
    [data-testid="stMetric"] { background:#fff; border:1px solid #e4eaf0; border-radius:12px; padding:14px 16px; box-shadow:0 2px 8px rgba(26,49,72,.035); }
    [data-testid="stMetricLabel"] { color:#66788a; font-size:.84rem; }
    [data-testid="stMetricValue"] { color:#142b45; font-size:1.48rem; }
    .eyebrow { color:#527089; font-size:.73rem; letter-spacing:.12em; font-weight:700; margin:0 0 .4rem; }
    .eyebrow span { color:#a4b2bf; padding:0 .3rem; }
    .section-kicker { color:#527089; font-size:.73rem; letter-spacing:.12em; font-weight:750; border-bottom:1px solid #e0e7ee; padding:1.05rem 0 .55rem; margin:1.25rem 0 .75rem; }
    .insight-card { background:#fff; border:1px solid #e2e9ef; border-left:4px solid #2463a6; border-radius:10px; padding:15px 17px; margin:.25rem 0 .9rem; box-shadow:0 2px 8px rgba(26,49,72,.03); min-height:135px; }
    .insight-card.amber { border-left-color:#c47a12; } .insight-card.green { border-left-color:#27845b; } .insight-card.blue { border-left-color:#2463a6; }
    .insight-card.priority { border-left-width:7px; background:#fffaf1; min-height:160px; padding:18px 20px; }
    .insight-card.priority .insight-fact { font-size:1.15rem; }
    .insight-label { text-transform:uppercase; letter-spacing:.09em; font-size:.68rem; font-weight:750; color:#66788a; margin-bottom:.42rem; }
    .insight-fact { color:#142b45; font-size:1.02rem; font-weight:700; line-height:1.35; margin-bottom:.7rem; }
    .insight-row { display:grid; grid-template-columns:125px 1fr; gap:8px; margin-top:.34rem; color:#516579; font-size:.82rem; line-height:1.4; }
    .insight-row b { color:#304960; }
    div[data-testid="stTabs"] button { font-weight:650; }
    </style>""", unsafe_allow_html=True)

    try:
        tables, summaries = load_all()
    except (FileNotFoundError, ValueError) as exc:
        st.error(f"RetailPulse cannot load its Phase 2 analytical layer. {exc} Run `.venv\\Scripts\\python.exe analysis.py` from the project root to regenerate the processed outputs.")
        st.stop()
    kpi = summaries["kpi_summary"]
    cancel = summaries["cancellation_return_summary"]
    validation = summaries["validation_report"]

    st.sidebar.markdown("## ◉ RetailPulse AI")
    st.sidebar.caption("RETAIL REVENUE & CUSTOMER INTELLIGENCE")
    page = st.sidebar.radio("Navigate", NAV, label_visibility="collapsed")
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Analysis filters")
    daily = tables["time_series_daily"].copy()
    dates = pd.to_datetime(daily.day, errors="coerce").dropna()
    min_date = dates.min().date() if not dates.empty else pd.Timestamp("2009-12-01").date()
    max_date = dates.max().date() if not dates.empty else pd.Timestamp("2011-12-09").date()
    selected_dates = st.sidebar.date_input("Chart date range", value=(min_date, max_date), min_value=min_date, max_value=max_date, help="Filters monthly trend charts. Validated headline KPIs remain full-period.", key="chart_dates")
    if isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 2:
        date_range = (selected_dates[0], selected_dates[1])
    elif isinstance(selected_dates, (tuple, list)) and len(selected_dates) == 1:
        date_range = (selected_dates[0], selected_dates[0])
    else:
        date_range = (min_date, max_date)
    countries = sorted(tables["country_metrics"].country.dropna().astype(str).unique().tolist())
    selected_countries = st.sidebar.multiselect("Markets", countries, default=[], help="Applies to country comparisons and return-by-market views.", key="markets")
    product_options = make_product_options(tables["product_metrics"])
    selected_product_label = st.sidebar.selectbox("Product", [x[0] for x in product_options], index=0, help="Applies to product rankings and product trend.", key="product")
    selected_product_code = dict(product_options).get(selected_product_label, "All products")
    segment_options = tables["cluster_profiles"].sort_values("cluster_id").segment.astype(str).tolist()
    selected_segments = st.sidebar.multiselect("Customer segments", segment_options, default=[], help="Applies to customer and segment recommendation views.", key="customer_segments")
    if st.sidebar.button("Reset filters", width="stretch"):
        for key in ["chart_dates", "markets", "product", "customer_segments"]:
            st.session_state.pop(key, None)
        st.rerun()
    st.sidebar.markdown("---")
    st.sidebar.caption("Chart filters apply only where the Phase 2 output supports that dimension. Headline KPIs and comparable Jan–Nov growth use their validated fixed scopes.")
    st.sidebar.caption("Source: UCI Online Retail II · GBP · Phase 2 analytical outputs")

    if page == "Executive Overview":
        executive_page(tables, kpi, cancel, validation, date_range, selected_countries, selected_product_code)
    elif page == "Customer Intelligence":
        customer_page(tables, kpi, selected_segments)
    elif page == "Product & Market Intelligence":
        product_market_page(tables, date_range, selected_countries, selected_product_code)
    else:
        risks_page(tables, kpi, cancel, validation, selected_countries, selected_segments)


if __name__ == "__main__":
    main()
