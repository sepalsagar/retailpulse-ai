"""Reproducible Phase 2 analytical pipeline for RetailPulse AI.

Run from the repository root with: .venv\\Scripts\\python.exe analysis.py
The source workbook is read-only. Compact dashboard-ready outputs are written
to data/processed/; raw transaction lines are not exported.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow
import sklearn
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parent
WORKBOOK = ROOT / "data" / "online_retail_II.xlsx"
OUT = ROOT / "data" / "processed"
SEED = 42
K_CANDIDATES = range(2, 7)
SELECTED_K = 4  # Reviewed against the printed profiles after the evaluation run.
SEGMENT_NAMES = {
    0: "Low-Value Inactive",
    1: "Recent Developing",
    2: "Valuable At Risk",
    3: "High-Value Active",
}


def write_table(frame: pd.DataFrame, name: str) -> None:
    """Write a compact Parquet table using stable column types where practical."""
    frame.to_parquet(OUT / f"{name}.parquet", index=False, compression="zstd")


def save_json(value: dict, name: str) -> None:
    (OUT / name).write_text(
        json.dumps(value, indent=2, ensure_ascii=False, default=str, allow_nan=False),
        encoding="utf-8",
    )


def normalize_sheet(raw: pd.DataFrame) -> pd.DataFrame:
    """Map the workbook's observed schema to compact, consistent fields."""
    d = raw.rename(
        columns={
            "Invoice": "invoice",
            "StockCode": "stock_code",
            "Description": "description",
            "Quantity": "quantity",
            "InvoiceDate": "invoice_date",
            "Price": "unit_price",
            "Customer ID": "customer_id_raw",
            "Country": "country",
        }
    )
    d["invoice"] = d.invoice.astype("string").str.strip()
    d["stock_code"] = d.stock_code.astype("string").str.strip()
    d["description"] = d.description.astype("string").str.strip()
    d["country"] = d.country.astype("string").str.strip()
    code = d.stock_code.str.upper()
    desc = d.description.str.upper()
    d["product_classification"] = np.select(
        [
            code.isin(["POST", "DOT", "C2"]) | desc.str.contains(r"POSTAGE|CARRIAGE|SHIPPING|DELIVERY", regex=True, na=False),
            code.isin(["M", "B", "D", "S", "ADJUST", "ADJUST2", "AMAZONFEE", "BANK CHARGES", "CRUK", "TEST001", "TEST002"]) | desc.str.contains(r"ADJUST BAD DEBT|BANK CHARGES|AMAZON FEE|COMMISSION|DISCOUNT|SAMPLES|THIS IS A TEST", regex=True, na=False),
            code.str.startswith("GIFT_", na=False),
        ],
        ["Service", "Adjustment", "Gift Voucher"],
        default="Merchandise",
    )
    # IDs are numeric in the workbook but are identifiers, not measurements.
    d["customer_id"] = d.customer_id_raw.map(
        lambda x: pd.NA if pd.isna(x) else str(int(x))
    ).astype("string")
    d["quantity"] = pd.to_numeric(d.quantity, errors="coerce").astype("int64")
    d["unit_price"] = pd.to_numeric(d.unit_price, errors="coerce").astype("float64")
    d["invoice_date"] = pd.to_datetime(d.invoice_date, errors="coerce")
    d["line_value"] = d.quantity * d.unit_price
    d["is_c_invoice"] = d.invoice.str.upper().str.startswith("C", na=False)
    d["is_negative_quantity"] = d.quantity.lt(0)
    d["is_negative_price"] = d.unit_price.lt(0)
    d["is_zero_price"] = d.unit_price.eq(0)
    d["is_zero_quantity"] = d.quantity.eq(0)
    return d.drop(columns=["customer_id_raw"])


def duplicate_audit(raw: pd.DataFrame, prior_hashes: set[int], duplicate_multiplicity: dict[int, int]) -> tuple[pd.Series, dict]:
    """Count exact same-sheet duplicates and cross-sheet repeated row fingerprints."""
    cols = list(raw.columns)
    same_sheet_dup = raw.duplicated(subset=cols, keep="first")
    hashes = pd.util.hash_pandas_object(raw[cols], index=False)
    cross_sheet_dup = hashes.isin(prior_hashes)
    all_dup = same_sheet_dup | cross_sheet_dup
    for h, count in hashes.value_counts(sort=False).items():
        h_int = int(h)
        previous_count = duplicate_multiplicity.get(h_int, 1 if h_int in prior_hashes else 0)
        final_count = previous_count + int(count)
        if final_count > 1:
            duplicate_multiplicity[h_int] = final_count
    prior_hashes.update(map(int, hashes.unique()))
    metrics = {
        "same_sheet_duplicate_occurrences": int(same_sheet_dup.sum()),
        "matching_prior_sheet_occurrences": int(cross_sheet_dup.sum()),
        "global_duplicate_occurrences": int(all_dup.sum()),
    }
    return all_dup, metrics


def add_to(total: dict, key: str, value) -> None:
    total[key] = total.get(key, 0) + value


def aggregate(frame: pd.DataFrame, keys: list[str], prefix: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=keys + [f"{prefix}_revenue", f"{prefix}_units", f"{prefix}_lines", f"{prefix}_orders"])
    return (
        frame.groupby(keys, dropna=False, observed=True)
        .agg(
            **{
                f"{prefix}_revenue": ("line_value", "sum"),
                f"{prefix}_units": ("quantity", "sum"),
                f"{prefix}_lines": ("invoice", "size"),
                f"{prefix}_orders": ("invoice", "nunique"),
            }
        )
        .reset_index()
    )


def main() -> None:
    if not WORKBOOK.exists():
        raise FileNotFoundError(f"Required workbook not found: {WORKBOOK}")
    OUT.mkdir(parents=True, exist_ok=True)

    xls = pd.ExcelFile(WORKBOOK, engine="openpyxl")
    if not xls.sheet_names:
        raise ValueError("Workbook contains no worksheets")

    all_hashes: set[int] = set()
    duplicate_multiplicity: dict[int, int] = {}
    totals: dict = {"sheets": {}, "signals": {}, "duplicate_impact": {}}
    sum_frames: dict[str, list[pd.DataFrame]] = {
        k: [] for k in ["daily", "monthly", "customer", "customer_returns", "product", "product_month", "country", "country_month", "returns_product", "returns_country", "invoice", "invoice_product", "product_description"]
    }
    n_raw = n_clean = 0
    raw_columns: list[str] | None = None
    raw_missing: dict[str, int] = {}
    type_report: dict[str, str] = {}
    all_revenue_raw = 0.0
    duplicate_removed_value = 0.0
    duplicate_removed_sales_value = 0.0
    duplicate_removed_sales_units = 0
    duplicate_removed_sales_lines = 0
    duplicate_affected_sales_invoices: set[str] = set()
    sale_invoices_before_dedup: set[str] = set()
    sale_invoices_after_dedup: set[str] = set()
    missing_customer_sale_value = identified_customer_sale_value = 0.0
    missing_customer_sales_lines = identified_customer_sales_lines = 0
    max_date: pd.Timestamp | None = None
    min_date: pd.Timestamp | None = None
    latest_valid_sale_date: pd.Timestamp | None = None
    negative_price_adjustment_value = 0.0
    c_invoice_quantity_anomaly_value = 0.0
    zero_price_positive_quantity_units = 0

    for sheet in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=sheet, engine="openpyxl")
        if raw_columns is None:
            raw_columns = list(raw.columns)
            type_report = {str(c): str(raw[c].dtype) for c in raw.columns}
            raw_missing = {str(c): int(raw[c].isna().sum()) for c in raw.columns}
        else:
            for c in raw.columns:
                raw_missing[str(c)] += int(raw[c].isna().sum())
        n_raw += len(raw)
        all_revenue_raw += float((raw["Quantity"] * raw["Price"]).sum())
        dup_mask, dup_metrics = duplicate_audit(raw, all_hashes, duplicate_multiplicity)
        duplicate_metrics = raw.loc[dup_mask]
        duplicate_removed_value += float((duplicate_metrics.Quantity * duplicate_metrics.Price).sum())

        d = normalize_sheet(raw)
        c = d.is_c_invoice
        negq = d.is_negative_quantity
        posq = d.quantity.gt(0)
        posp = d.unit_price.gt(0)
        negp = d.is_negative_price
        zerop = d.is_zero_price
        # Keep rule signals disjoint enough for interpretation; raw flags remain in audit.
        is_numeric_invoice = d.invoice.str.fullmatch(r"\d+", na=False)
        d["record_type"] = np.select(
            [
                c & negq & posp & d.product_classification.eq("Merchandise"),
                c & negq & posp & d.product_classification.eq("Service"),
                c & ~negq,
                d.invoice.str.upper().str.startswith("A", na=False),
                d.product_classification.eq("Adjustment"),
                d.product_classification.eq("Gift Voucher"),
                d.product_classification.eq("Service"),
                ~c & negq & posp,
                ~c & negq & ~posp,
                negp,
                ~c & posq & zerop,
                (~c) & is_numeric_invoice & d.product_classification.eq("Merchandise") & posq & posp,
            ],
            ["merchandise_c_return", "service_cancellation", "c_invoice_quantity_anomaly", "accounting_adjustment_invoice", "non_sales_adjustment", "gift_voucher", "service_charge", "uncoded_return_with_value", "uncoded_negative_quantity_zero_or_negative_price", "negative_price_adjustment", "zero_price_positive_quantity", "ordinary_merchandise_sale"],
            default="other_zero_or_unclassified",
        )
        # Separate priced merchandise from shipping/services and adjustments.
        d["is_ordinary_sale"] = (~c) & is_numeric_invoice & d.product_classification.eq("Merchandise") & posq & posp & (~negp)
        d["is_service_sale"] = (~c) & is_numeric_invoice & d.product_classification.eq("Service") & posq & posp
        # Defensible monetary returns, separated by merchandise and service.
        d["is_return"] = c & negq & posp & d.product_classification.eq("Merchandise")
        d["is_service_return"] = c & negq & posp & d.product_classification.eq("Service")
        d["is_duplicate"] = dup_mask.to_numpy()
        d["is_eligible_sale"] = d.is_ordinary_sale & ~d.is_duplicate
        d["is_eligible_return"] = d.is_return & ~d.is_duplicate
        sale_invoices_before_dedup.update(d.loc[d.is_ordinary_sale, "invoice"].dropna().astype(str).unique())

        add_to(totals["signals"], "c_prefix_rows", int(c.sum()))
        add_to(totals["signals"], "c_prefix_negative_quantity", int((c & negq).sum()))
        add_to(totals["signals"], "c_prefix_nonnegative_quantity", int((c & ~negq).sum()))
        add_to(totals["signals"], "negative_quantity_rows", int(negq.sum()))
        add_to(totals["signals"], "negative_quantity_without_c_prefix", int((~c & negq).sum()))
        add_to(totals["signals"], "negative_quantity_zero_price", int((negq & zerop).sum()))
        add_to(totals["signals"], "negative_price_rows", int(negp.sum()))
        negative_price_adjustment_value += float(d.loc[negp, "line_value"].sum())
        c_invoice_quantity_anomaly_value += float(d.loc[c & ~negq, "line_value"].sum())
        add_to(totals["signals"], "zero_price_rows", int(zerop.sum()))
        zero_price_positive_quantity_units += int(d.loc[(~c) & posq & zerop, "quantity"].sum())
        add_to(totals["signals"], "zero_quantity_rows", int(d.is_zero_quantity.sum()))
        add_to(totals["signals"], "c_return_value_rows", int((c & negq & posp).sum()))
        add_to(totals["signals"], "c_return_zero_price_rows", int((c & negq & zerop).sum()))

        # Measure what removing duplicates changes, before filtering them.
        dup_sales = d.is_ordinary_sale & d.is_duplicate
        duplicate_removed_sales_value += float(d.loc[dup_sales, "line_value"].sum())
        duplicate_removed_sales_units += int(d.loc[dup_sales, "quantity"].sum())
        duplicate_removed_sales_lines += int(dup_sales.sum())
        duplicate_affected_sales_invoices.update(d.loc[dup_sales, "invoice"].dropna().astype(str).unique())

        # Sheet-level diagnostics use actual dates, not worksheet titles.
        dtmin, dtmax = d.invoice_date.min(), d.invoice_date.max()
        min_date = dtmin if min_date is None else min(min_date, dtmin)
        max_date = dtmax if max_date is None else max(max_date, dtmax)
        totals["sheets"][sheet] = {
            "rows": int(len(d)),
            "date_min": str(dtmin),
            "date_max": str(dtmax),
            "missing": {str(cn): int(raw[cn].isna().sum()) for cn in raw.columns},
            "same_sheet_duplicate_occurrences": dup_metrics["same_sheet_duplicate_occurrences"],
            "matching_prior_sheet_occurrences": dup_metrics["matching_prior_sheet_occurrences"],
            "global_duplicate_occurrences": dup_metrics["global_duplicate_occurrences"],
            "in_memory_frame_mb": round(float(d.memory_usage(deep=True).sum() / 1024**2), 1),
            "record_type_counts": {str(k): int(v) for k, v in d.record_type.value_counts().items()},
        }

        # Filter out exact repeated full-row records before creating analytic tables.
        a = d.loc[~d.is_duplicate].copy()
        n_clean += len(a)
        sales = a.loc[a.is_ordinary_sale].copy()
        services = a.loc[a.is_service_sale].copy()
        sale_invoices_after_dedup.update(sales.invoice.dropna().astype(str).unique())
        if not sales.empty:
            sale_max = sales.invoice_date.max()
            latest_valid_sale_date = sale_max if latest_valid_sale_date is None else max(latest_valid_sale_date, sale_max)
        returns = a.loc[a.is_return].copy()
        service_returns = a.loc[a.is_service_return].copy()
        gross = float(sales.line_value.sum())
        return_value = float(returns.line_value.sum())
        add_to(totals, "service_charge_revenue", float(services.line_value.sum()))
        add_to(totals, "service_charge_lines", int(len(services)))
        add_to(totals, "service_cancellation_value", float(service_returns.line_value.sum()))
        add_to(totals, "service_cancellation_lines", int(len(service_returns)))
        add_to(totals, "non_sales_adjustment_value_before_dedup", float(d.loc[d.product_classification.eq("Adjustment"), "line_value"].sum()))
        add_to(totals, "gift_voucher_value_before_dedup", float(d.loc[d.product_classification.eq("Gift Voucher"), "line_value"].sum()))
        add_to(totals, "gross_positive_sales_value", gross)
        add_to(totals, "signed_cancellation_return_value", return_value)
        add_to(totals, "ordinary_sale_lines", int(len(sales)))
        add_to(totals, "ordinary_sale_units", int(sales.quantity.sum()))
        add_to(totals, "ordinary_sale_invoices", int(sales.invoice.nunique()))
        add_to(totals, "return_lines", int(len(returns)))
        add_to(totals, "return_units_signed", int(returns.quantity.sum()))
        add_to(totals, "return_invoice_count", int(returns.invoice.nunique()))
        identified = sales.customer_id.notna()
        identified_customer_sale_value += float(sales.loc[identified, "line_value"].sum())
        missing_customer_sale_value += float(sales.loc[~identified, "line_value"].sum())
        identified_customer_sales_lines += int(identified.sum())
        missing_customer_sales_lines += int((~identified).sum())

        sales["day"] = sales.invoice_date.dt.floor("D")
        sales["month"] = sales.invoice_date.dt.to_period("M").astype(str)
        sales["year"] = sales.invoice_date.dt.year.astype("int16")
        returns["day"] = returns.invoice_date.dt.floor("D")
        # Compact aggregate tables; no million-row line file is created.
        sum_frames["daily"].append(aggregate(sales, ["day"], "sales"))
        sum_frames["monthly"].append(aggregate(sales, ["month"], "sales"))
        sum_frames["customer"].append(
            sales.loc[sales.customer_id.notna()].groupby("customer_id", observed=True).agg(
                monetary=("line_value", "sum"), frequency=("invoice", "nunique"),
                last_purchase=("invoice_date", "max"), first_purchase=("invoice_date", "min"),
                sale_lines=("invoice", "size"), units=("quantity", "sum"),
            ).reset_index()
        )
        if not returns.empty:
            sum_frames["customer_returns"].append(
                returns.loc[returns.customer_id.notna()].groupby("customer_id", observed=True).agg(
                    return_value=("line_value", "sum"), return_lines=("invoice", "size"),
                    return_units=("quantity", "sum"),
                ).reset_index()
            )
        sum_frames["product"].append(aggregate(sales, ["stock_code"], "sales"))
        descriptions = sales.loc[sales.description.notna() & sales.description.ne("")].groupby(["stock_code", "description"], observed=True).size().rename("description_rows").reset_index()
        sum_frames["product_description"].append(descriptions)
        sum_frames["product_month"].append(aggregate(sales, ["stock_code", "month"], "sales"))
        sum_frames["country"].append(aggregate(sales, ["country"], "sales"))
        sum_frames["country_month"].append(aggregate(sales, ["country", "month"], "sales"))
        sum_frames["invoice"].append(
            sales.groupby("invoice", observed=True).agg(
                order_date=("invoice_date", "max"), order_value=("line_value", "sum"),
                order_units=("quantity", "sum"), customer_id=("customer_id", "first"),
                country=("country", "first"), lines=("invoice", "size"),
            ).reset_index()
        )
        sum_frames["invoice_product"].append(
            sales.groupby(["invoice", "stock_code", "month"], observed=True).agg(
                product_revenue=("line_value", "sum"), product_units=("quantity", "sum"),
                customer_id=("customer_id", "first"), country=("country", "first"),
            ).reset_index()
        )
        if not returns.empty:
            sum_frames["returns_product"].append(aggregate(returns, ["stock_code"], "returns"))
            sum_frames["returns_country"].append(aggregate(returns, ["country"], "returns"))

        # Customer coverage at transaction-line level and revenue level.
        raw = None
        d = None
        a = sales = services = returns = service_returns = None

    # Merge per-sheet summaries without ever retaining two raw sheets.
    def combine(key: str, group_cols: list[str], agg_cols: list[str]) -> pd.DataFrame:
        fs = sum_frames[key]
        if not fs:
            return pd.DataFrame(columns=group_cols + agg_cols)
        joined = pd.concat(fs, ignore_index=True)
        if not group_cols:
            return joined
        # Additive measures can be summed; distinct invoice counts are recomputed
        # from the invoice-level table where needed rather than summed here.
        additive = [c for c in agg_cols if not c.endswith("_orders")]
        result = joined.groupby(group_cols, dropna=False, observed=True)[additive].sum().reset_index()
        return result

    daily = combine("daily", ["day"], ["sales_revenue", "sales_units", "sales_lines", "sales_orders"])
    monthly = combine("monthly", ["month"], ["sales_revenue", "sales_units", "sales_lines", "sales_orders"])
    customers = pd.concat(sum_frames["customer"], ignore_index=True).groupby("customer_id", observed=True).agg(
        monetary=("monetary", "sum"), frequency=("frequency", "sum"),
        last_purchase=("last_purchase", "max"), first_purchase=("first_purchase", "min"),
        sale_lines=("sale_lines", "sum"), units=("units", "sum"),
    ).reset_index()
    # Same invoice can occur across sheets: frequency must be distinct globally.
    invoice_table = pd.concat(sum_frames["invoice"], ignore_index=True).groupby("invoice", as_index=False, observed=True).agg(
        order_date=("order_date", "max"), order_value=("order_value", "sum"),
        order_units=("order_units", "sum"), customer_id=("customer_id", "first"),
        country=("country", "first"), lines=("lines", "sum"),
    )
    # Correct customer frequency and order counts using globally distinct invoices.
    global_cust_freq = invoice_table.loc[invoice_table.customer_id.notna()].groupby("customer_id").invoice.nunique()
    customers["frequency"] = customers.customer_id.map(global_cust_freq).fillna(0).astype(int)
    invoice_table["day"] = invoice_table.order_date.dt.floor("D")
    invoice_table["month"] = invoice_table.order_date.dt.to_period("M").astype(str)
    daily_orders = invoice_table.groupby("day").invoice.nunique().rename("sales_orders").reset_index()
    monthly_orders = invoice_table.groupby("month").invoice.nunique().rename("sales_orders").reset_index()
    daily = daily.drop(columns=[c for c in ["sales_orders"] if c in daily]).merge(daily_orders, on="day", how="left")
    monthly = monthly.drop(columns=[c for c in ["sales_orders"] if c in monthly]).merge(monthly_orders, on="month", how="left")
    reference_date = latest_valid_sale_date.normalize() + pd.Timedelta(days=1)
    customers["recency_days"] = (reference_date - customers.last_purchase.dt.normalize()).dt.days
    customers["customer_key"] = [f"C{i:05d}" for i in range(1, len(customers) + 1)]

    # Attribute monthly growth to actual invoice dates. Compare complete Jan-Nov windows.
    monthly["month_dt"] = pd.to_datetime(monthly.month + "-01")
    month_a = monthly.loc[(monthly.month_dt >= "2010-01-01") & (monthly.month_dt <= "2010-11-01")]
    month_b = monthly.loc[(monthly.month_dt >= "2011-01-01") & (monthly.month_dt <= "2011-11-01")]
    growth_2010 = float(month_a.sales_revenue.sum())
    growth_2011 = float(month_b.sales_revenue.sum())
    revenue_growth = (growth_2011 / growth_2010 - 1.0) if growth_2010 else None

    # Clustering: log-transform skewed positive RFM values, then standardize.
    features = pd.DataFrame({
        "log_recency": np.log1p(customers.recency_days.clip(lower=0)),
        "log_frequency": np.log1p(customers.frequency.clip(lower=0)),
        "log_monetary": np.log1p(customers.monetary.clip(lower=0)),
    })
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(features)
    eval_rows = []
    for k in K_CANDIDATES:
        model = KMeans(n_clusters=k, random_state=SEED, n_init=20)
        labels = model.fit_predict(x_scaled)
        sil = silhouette_score(x_scaled, labels, sample_size=min(2500, len(labels)), random_state=SEED)
        model2 = KMeans(n_clusters=k, random_state=SEED + 7, n_init=20)
        labels2 = model2.fit_predict(x_scaled)
        ari = adjusted_rand_score(labels, labels2)
        eval_rows.append({"k": k, "silhouette_sampled": float(sil), "seed_stability_ari": float(ari), "inertia": float(model.inertia_)})
    evaluation = pd.DataFrame(eval_rows)
    final_model = KMeans(n_clusters=SELECTED_K, random_state=SEED, n_init=30)
    customers["cluster_id"] = final_model.fit_predict(x_scaled).astype(int)
    profiles = customers.groupby("cluster_id").agg(
        customers=("customer_key", "size"), revenue=("monetary", "sum"),
        avg_monetary=("monetary", "mean"), median_monetary=("monetary", "median"),
        avg_frequency=("frequency", "mean"), median_frequency=("frequency", "median"),
        avg_recency_days=("recency_days", "mean"), median_recency_days=("recency_days", "median"),
    ).reset_index()
    profiles["segment"] = profiles.cluster_id.map(SEGMENT_NAMES)
    profiles["revenue_share"] = profiles.revenue / profiles.revenue.sum()
    customers = customers.merge(profiles[["cluster_id"]], on="cluster_id", how="left")
    customer_segment = customers[["customer_key", "cluster_id"]].copy()
    # Names were assigned after reviewing all candidate profiles and trade-offs.
    name_by_cluster = {int(r.cluster_id): SEGMENT_NAMES[int(r.cluster_id)] for r in profiles.itertuples()}
    customer_segment["segment"] = customer_segment.cluster_id.map(name_by_cluster)

    # Build product/country metrics and global distinct-order counts from invoice table.
    products = combine("product", ["stock_code"], ["sales_revenue", "sales_units", "sales_lines", "sales_orders"])
    description_counts = pd.concat(sum_frames["product_description"], ignore_index=True).groupby(["stock_code", "description"], as_index=False, observed=True).description_rows.sum()
    description_counts = description_counts.sort_values(["stock_code", "description_rows", "description"], ascending=[True, False, True])
    primary_descriptions = description_counts.drop_duplicates("stock_code").rename(columns={"description": "product_description", "description_rows": "description_support_rows"})
    products = products.merge(primary_descriptions, on="stock_code", how="left")
    products["product_description"] = products.product_description.fillna("Description unavailable")
    desc_text = products.product_description.str.upper()
    product_code_upper = products.stock_code.str.upper()
    products["product_classification"] = np.select(
        [
            product_code_upper.isin(["POST", "DOT", "C2"]) | desc_text.str.contains(r"POSTAGE|CARRIAGE|SHIPPING|DELIVERY", regex=True),
            product_code_upper.isin(["M", "B", "D", "S", "ADJUST", "ADJUST2", "AMAZONFEE", "BANK CHARGES", "CRUK", "TEST001", "TEST002"]) | desc_text.str.contains(r"ADJUST BAD DEBT|BANK CHARGES|AMAZON FEE|COMMISSION|DISCOUNT|SAMPLES|THIS IS A TEST", regex=True),
            product_code_upper.str.startswith("GIFT_", na=False),
        ],
        ["Service", "Adjustment", "Gift Voucher"], default="Merchandise",
    )
    product_month = combine("product_month", ["stock_code", "month"], ["sales_revenue", "sales_units", "sales_lines", "sales_orders"])
    countries = combine("country", ["country"], ["sales_revenue", "sales_units", "sales_lines", "sales_orders"])
    country_month = combine("country_month", ["country", "month"], ["sales_revenue", "sales_units", "sales_lines", "sales_orders"])
    country_month["month_dt"] = pd.to_datetime(country_month.month + "-01")
    invoice_product = pd.concat(sum_frames["invoice_product"], ignore_index=True).groupby(
        ["invoice", "stock_code", "month"], as_index=False, observed=True
    ).agg(product_revenue=("product_revenue", "sum"), product_units=("product_units", "sum"), customer_id=("customer_id", "first"), country=("country", "first"))
    product_orders = invoice_product.groupby("stock_code", observed=True).agg(
        sales_orders=("invoice", "nunique"), sales_customers=("customer_id", "nunique")
    ).reset_index()
    products = products.drop(columns=[c for c in ["sales_orders"] if c in products]).merge(product_orders, on="stock_code", how="left")
    product_month_orders = invoice_product.groupby(["stock_code", "month"], observed=True).invoice.nunique().rename("sales_orders").reset_index()
    product_month = product_month.drop(columns=[c for c in ["sales_orders"] if c in product_month]).merge(product_month_orders, on=["stock_code", "month"], how="left")
    country_month_orders = invoice_table.groupby(["country", "month"], dropna=False).invoice.nunique().rename("sales_orders").reset_index()
    country_month = country_month.drop(columns=[c for c in ["sales_orders"] if c in country_month]).merge(country_month_orders, on=["country", "month"], how="left")
    inv_country = invoice_table.groupby("country", dropna=False).agg(
        sales_orders_global=("invoice", "nunique"), sales_customers=("customer_id", "nunique")
    ).reset_index()
    countries = countries.merge(inv_country, on="country", how="left")
    countries["aov"] = countries.sales_revenue / countries.sales_orders_global.replace(0, np.nan)

    # Return aggregates and cancellation table, combined across sheets.
    def combine_return(key: str, group_cols: list[str]) -> pd.DataFrame:
        if not sum_frames[key]:
            return pd.DataFrame(columns=group_cols + ["returns_revenue", "returns_units", "returns_lines", "returns_orders"])
        z = pd.concat(sum_frames[key], ignore_index=True)
        sums = ["returns_revenue", "returns_units", "returns_lines"]
        out = z.groupby(group_cols, dropna=False, observed=True)[sums].sum().reset_index()
        return out

    returns_product = combine_return("returns_product", ["stock_code"])
    returns_country = combine_return("returns_country", ["country"])
    products = products.merge(returns_product, on="stock_code", how="left")
    countries = countries.merge(returns_country, on="country", how="left")
    for frame in [products, countries]:
        for col in ["returns_revenue", "returns_units", "returns_lines"]:
            if col in frame:
                frame[col] = frame[col].fillna(0)
    products = products.sort_values("sales_revenue", ascending=False).reset_index(drop=True)
    products["revenue_share"] = products.sales_revenue / products.sales_revenue.sum()
    products["cumulative_revenue_share"] = products.revenue_share.cumsum()
    products["return_line_rate"] = products.returns_lines / products.sales_lines.replace(0, np.nan)
    products["unusual_volume_flag"] = products.sales_units.ge(10000) & products.sales_orders.le(5)
    products["net_units_after_defined_returns"] = products.sales_units + products.returns_units
    countries["return_line_rate"] = countries.returns_lines / countries.sales_lines.replace(0, np.nan)

    product_ref = products.set_index("stock_code").sales_revenue
    product_month["month_dt"] = pd.to_datetime(product_month.month + "-01")
    p10 = product_month.loc[(product_month.month_dt >= "2010-01-01") & (product_month.month_dt <= "2010-11-01")].groupby("stock_code").sales_revenue.sum()
    p11 = product_month.loc[(product_month.month_dt >= "2011-01-01") & (product_month.month_dt <= "2011-11-01")].groupby("stock_code").sales_revenue.sum()
    pg = pd.concat([p10.rename("revenue_2010_jan_nov"), p11.rename("revenue_2011_jan_nov")], axis=1).fillna(0).reset_index()
    pg["revenue_growth"] = np.where(pg.revenue_2010_jan_nov.gt(0), pg.revenue_2011_jan_nov / pg.revenue_2010_jan_nov - 1, np.nan)
    products = products.merge(pg[["stock_code", "revenue_2010_jan_nov", "revenue_2011_jan_nov", "revenue_growth"]], on="stock_code", how="left")
    p10_orders = invoice_product.loc[invoice_product.month.between("2010-01", "2010-11")].groupby("stock_code").invoice.nunique().rename("orders_2010_jan_nov")
    p11_orders = invoice_product.loc[invoice_product.month.between("2011-01", "2011-11")].groupby("stock_code").invoice.nunique().rename("orders_2011_jan_nov")
    p_orders = pd.concat([p10_orders, p11_orders], axis=1).fillna(0).reset_index()
    products = products.merge(p_orders, on="stock_code", how="left")
    countries["revenue_share"] = countries.sales_revenue / countries.sales_revenue.sum()

    # Recompute country order/customer AOV from globally unique invoice records.
    inv_country = invoice_table.groupby("country", dropna=False).agg(
        sales_orders_global=("invoice", "nunique"), sales_customers=("customer_id", "nunique")
    ).reset_index()
    countries = countries.drop(columns=[c for c in ["sales_orders_global", "sales_customers", "aov"] if c in countries])
    countries = countries.merge(inv_country, on="country", how="left")
    countries["aov"] = countries.sales_revenue / countries.sales_orders_global.replace(0, np.nan)
    c10 = country_month.loc[(country_month.month_dt >= "2010-01-01") & (country_month.month_dt <= "2010-11-01")].groupby("country").sales_revenue.sum()
    c11 = country_month.loc[(country_month.month_dt >= "2011-01-01") & (country_month.month_dt <= "2011-11-01")].groupby("country").sales_revenue.sum()
    cg = pd.concat([c10.rename("revenue_2010_jan_nov"), c11.rename("revenue_2011_jan_nov")], axis=1).fillna(0).reset_index()
    cg["revenue_growth"] = np.where(cg.revenue_2010_jan_nov.gt(0), cg.revenue_2011_jan_nov / cg.revenue_2010_jan_nov - 1, np.nan)
    countries = countries.merge(cg[["country", "revenue_2010_jan_nov", "revenue_2011_jan_nov", "revenue_growth"]], on="country", how="left")
    c10_orders = invoice_table.loc[invoice_table.month.between("2010-01", "2010-11")].groupby("country").invoice.nunique().rename("orders_2010_jan_nov")
    c11_orders = invoice_table.loc[invoice_table.month.between("2011-01", "2011-11")].groupby("country").invoice.nunique().rename("orders_2011_jan_nov")
    c_orders = pd.concat([c10_orders, c11_orders], axis=1).fillna(0).reset_index()
    countries = countries.merge(c_orders, on="country", how="left")

    # Customer key mapping in all customer-facing outputs; raw IDs are not exported.
    customers["monetary_percentile"] = customers.monetary.rank(pct=True)
    customers["recency_percentile"] = customers.recency_days.rank(pct=True)
    sales_activity = pd.concat(sum_frames["customer"], ignore_index=True)
    sales_activity["month"] = sales_activity.last_purchase.dt.to_period("M").astype(str)
    # Exact 6-month spending windows at the end of the observed complete period.
    # The prior window is Dec 2010-May 2011; recent is Jun-Nov 2011.
    cust_windows = pd.DataFrame({"customer_id": customers.customer_id})
    # Build window spend from month-level data tracked while streaming via customer months.
    # Transaction month allocations are reconstructed from invoice aggregates below.
    cust_periods = invoice_table.loc[invoice_table.customer_id.notna()].copy()
    cust_periods["month"] = cust_periods.order_date.dt.to_period("M").astype(str)
    recent = cust_periods.loc[cust_periods.month.between("2011-06", "2011-11")].groupby("customer_id").order_value.sum()
    previous = cust_periods.loc[cust_periods.month.between("2010-12", "2011-05")].groupby("customer_id").order_value.sum()
    customers["recent_6m_spend"] = customers.customer_id.map(recent).fillna(0)
    customers["prior_6m_spend"] = customers.customer_id.map(previous).fillna(0)
    customers["activity_decline"] = np.where(
        customers.prior_6m_spend.gt(0),
        (1 - customers.recent_6m_spend / customers.prior_6m_spend).clip(0, 1),
        np.where(customers.recent_6m_spend.eq(0), 1.0, 0.0),
    )
    cust_return = pd.concat(sum_frames["customer_returns"], ignore_index=True) if sum_frames["customer_returns"] else pd.DataFrame(columns=["customer_id", "return_value", "return_lines", "return_units"])
    if not cust_return.empty:
        cust_return = cust_return.groupby("customer_id").agg(return_value=("return_value", "sum"), return_lines=("return_lines", "sum"), return_units=("return_units", "sum")).reset_index()
        customers = customers.merge(cust_return, on="customer_id", how="left")
    else:
        customers["return_value"] = 0.0
        customers["return_lines"] = 0
        customers["return_units"] = 0
    customers[["return_value", "return_lines", "return_units"]] = customers[["return_value", "return_lines", "return_units"]].fillna(0)
    customers["return_value_ratio"] = (-customers.return_value / customers.monetary.replace(0, np.nan)).clip(0, 1).fillna(0)
    customers["risk_score"] = 100 * (
        0.50 * customers.recency_percentile
        + 0.30 * customers.activity_decline
        + 0.20 * customers.return_value_ratio
    )
    customers["risk_level"] = pd.cut(customers.risk_score, [-0.01, 50, 75, 100.01], labels=["Low", "Moderate", "High"], include_lowest=True).astype(str)
    customers["risk_priority"] = customers.risk_score * (0.5 + 0.5 * customers.monetary_percentile)
    customers["is_high_value_at_risk"] = (customers.monetary_percentile >= 0.75) & (customers.risk_score >= 60)
    customers["recommended_action"] = np.select(
        [customers.is_high_value_at_risk, customers.risk_level.eq("High"), customers.activity_decline.ge(0.5)],
        ["Personalized retention outreach; verify recent service or product issues.", "Low-cost reactivation message; monitor response before offering incentives.", "Review recent purchase mix and test a relevant reminder."],
        default="Maintain regular service and monitor purchase cadence.",
    )
    risk = customers.sort_values(["risk_priority", "monetary"], ascending=False).copy()
    risk["segment"] = risk.cluster_id.map(name_by_cluster)
    risk = risk.rename(columns={"recency_days": "recency", "frequency": "frequency_orders", "monetary": "customer_value"})
    risk = risk[["customer_key", "segment", "risk_level", "risk_score", "risk_priority", "customer_value", "recency", "frequency_orders", "activity_decline", "recommended_action", "is_high_value_at_risk"]]

    # KPI formula validation using distinct invoice-level order table and independent sums.
    total_gross = float(totals.get("gross_positive_sales_value", 0.0))
    total_return = float(totals.get("signed_cancellation_return_value", 0.0))
    service_gross = float(totals.get("service_charge_revenue", 0.0))
    service_return = float(totals.get("service_cancellation_value", 0.0))
    total_orders = int(invoice_table.invoice.nunique())
    total_customers = int(invoice_table.customer_id.nunique())
    total_units = int(totals.get("ordinary_sale_units", 0))
    customer_revenue = float(customers.monetary.sum())
    top10_share = float(customers.nlargest(10, "monetary").monetary.sum() / customer_revenue) if customer_revenue else 0.0
    repeat_customers = int(customers.frequency.gt(1).sum())
    cancellation_line_rate = int(totals.get("return_lines", 0)) / max(int(totals.get("ordinary_sale_lines", 0)), 1)
    kpis = {
        "currency": "GBP (£), as specified in UCI dataset metadata.",
        "revenue_scope": "Gross eligible positive-price merchandise value; excludes service charges and adjustments and is not net of returns.",
        "gross_positive_sales_value": total_gross,
        "signed_cancellation_return_value": total_return,
        "net_sales_after_defined_returns": total_gross + total_return,
        "service_charge_revenue_separate": service_gross,
        "service_charge_cancellation_value_separate": service_return,
        "gross_transaction_value_including_service_charges": total_gross + service_gross,
        "net_transaction_value_including_defined_returns": total_gross + total_return + service_gross + service_return,
        "orders": total_orders,
        "customers": total_customers,
        "units": total_units,
        "average_order_value": total_gross / total_orders if total_orders else None,
        "revenue_per_identified_customer": customer_revenue / total_customers if total_customers else None,
        "c_invoice_return_line_rate": cancellation_line_rate,
        "c_invoice_return_lines": int(totals.get("return_lines", 0)),
        "ordinary_sale_lines": int(totals.get("ordinary_sale_lines", 0)),
        "zero_price_positive_quantity_rows": int(totals["signals"].get("zero_price_rows", 0) - totals["signals"].get("negative_quantity_zero_price", 0)),
        "zero_price_positive_quantity_units_separate": zero_price_positive_quantity_units,
        "negative_price_adjustment_value_excluded": negative_price_adjustment_value,
        "c_invoice_nonnegative_quantity_adjustment_value_excluded": c_invoice_quantity_anomaly_value,
        "repeat_customer_rate": repeat_customers / total_customers if total_customers else None,
        "revenue_growth_2011_jan_nov_vs_2010_jan_nov": revenue_growth,
        "growth_revenue_2010_jan_nov": growth_2010,
        "growth_revenue_2011_jan_nov": growth_2011,
        "top_10_identified_customer_revenue_share": top10_share,
        "top_10_merchandise_product_revenue_share": float(products.nlargest(10, "sales_revenue").sales_revenue.sum() / total_gross) if total_gross else None,
        "formulas": {
            "gross_positive_sales_value": "sum(Quantity * Price) for numeric-invoice, non-C, positive-quantity, positive-price Merchandise lines, after exact-row deduplication; service charges are separate",
            "signed_cancellation_return_value": "sum(Quantity * Price) for C-prefixed, negative-quantity, positive-price Merchandise lines, after exact-row deduplication; negative signed amount",
            "net_sales_after_defined_returns": "gross merchandise sales + signed merchandise cancellation/return value; excludes service charges, adjustments, vouchers, zero-price lines, and unpriced quantity signals",
            "service_charge_revenue_separate": "positive value of eligible numeric-invoice non-C postage/carriage/shipping lines; shown separately from merchandise revenue",
            "net_transaction_value_including_defined_returns": "net merchandise sales + service charges + signed service cancellation value",
            "orders": "distinct invoices with at least one eligible ordinary-sale line",
            "customers": "distinct non-missing Customer IDs on eligible ordinary-sale invoices",
            "units": "sum Quantity > 0 only where invoice is non-C-prefixed and UnitPrice > 0, after exact-row deduplication; zero-price quantities are reported separately",
            "average_order_value": "gross positive-sale value / distinct eligible ordinary-sale invoices",
            "revenue_per_identified_customer": "identified-customer eligible sales value / distinct identified eligible-sale customers",
            "c_invoice_return_line_rate": "eligible C-prefixed negative-quantity return lines / eligible ordinary-sale lines",
            "repeat_customer_rate": "identified customers with more than one distinct eligible order / identified eligible-sale customers",
            "revenue_growth": "(Jan-Nov 2011 eligible gross sales / Jan-Nov 2010 eligible gross sales) - 1; both are complete 11-month calendar windows",
            "customer_revenue_concentration": "eligible identified-customer revenue for top 10 customers / all eligible identified-customer revenue",
        },
    }

    # Correct invoice and customer order counts, plus product distinct order/customer contributions.
    kpis["identified_customer_transaction_lines"] = identified_customer_sales_lines
    kpis["unidentified_customer_transaction_lines"] = missing_customer_sales_lines
    kpis["raw_transaction_lines_with_customer_id"] = n_raw - raw_missing.get("Customer ID", 0)
    kpis["raw_transaction_lines_without_customer_id"] = raw_missing.get("Customer ID", 0)
    kpis["raw_transaction_customer_id_coverage"] = (n_raw - raw_missing.get("Customer ID", 0)) / n_raw if n_raw else None
    kpis["identified_customer_revenue"] = identified_customer_sale_value
    kpis["unidentified_customer_revenue"] = missing_customer_sale_value
    kpis["identified_customer_revenue_share"] = identified_customer_sale_value / total_gross if total_gross else None
    kpis["unidentified_customer_revenue_share"] = missing_customer_sale_value / total_gross if total_gross else None
    kpis["identified_customer_line_share"] = identified_customer_sales_lines / max(int(totals.get("ordinary_sale_lines", 0)), 1)
    kpis["unidentified_customer_line_share"] = missing_customer_sales_lines / max(int(totals.get("ordinary_sale_lines", 0)), 1)

    # Evidence-based opportunity records with explicit minimum support.
    opportunities: list[dict] = []
    if customers.is_high_value_at_risk.any():
        n = int(customers.is_high_value_at_risk.sum())
        v = float(customers.loc[customers.is_high_value_at_risk, "monetary"].sum())
        opportunities.append({"category": "High-value at-risk customers", "evidence": f"{n} customers are in the top monetary quartile and have risk score >= 60; their eligible historical sales total {v:,.2f}.", "business_meaning": "A meaningful pool of previously valuable customers may need retention attention.", "recommended_action": "Prioritize personalized service outreach and test a relevant replenishment or feedback message.", "priority": "High"})
    eligible_market = countries.loc[countries.orders_2010_jan_nov.ge(100) & countries.orders_2011_jan_nov.ge(100) & countries.revenue_growth.ge(0.10)]
    for r in eligible_market.sort_values("revenue_growth", ascending=False).itertuples():
        opportunities.append({"category": "Growing market", "evidence": f"{r.country}: Jan-Nov eligible revenue growth {r.revenue_growth:.1%}; {int(r.orders_2010_jan_nov)} orders in Jan-Nov 2010 and {int(r.orders_2011_jan_nov)} in Jan-Nov 2011.", "business_meaning": "The market shows positive growth with enough order volume in both comparison windows to warrant attention.", "recommended_action": f"Review capacity and customer acquisition options for {r.country}; validate margin and repeat behavior before expanding spend.", "priority": "Medium"})
    eligible_products = products.loc[(products.product_classification == "Merchandise") & products.revenue_2010_jan_nov.gt(0) & products.revenue_2011_jan_nov.gt(0) & products.revenue_growth.ge(0.20) & products.orders_2010_jan_nov.ge(20) & products.orders_2011_jan_nov.ge(20)]
    for r in eligible_products.nlargest(10, "sales_revenue").itertuples():
        opportunities.append({"category": "Strong growing product", "evidence": f"Stock code {r.stock_code}: Jan-Nov revenue growth {r.revenue_growth:.1%}; eligible revenue {r.sales_revenue:,.2f}; {int(r.orders_2010_jan_nov)} orders in Jan-Nov 2010 and {int(r.orders_2011_jan_nov)} in Jan-Nov 2011.", "business_meaning": "A product with repeated order presence and comparable-period sales growth may merit supply and placement review.", "recommended_action": f"Check stock availability and product presentation for {r.stock_code}; monitor margin and return rate before increasing inventory.", "priority": "Medium"})
    if top10_share >= 0.25:
        opportunities.append({"category": "Customer revenue concentration", "evidence": f"Top 10 identified customers contribute {top10_share:.1%} of eligible identified-customer revenue.", "business_meaning": "A small number of accounts account for a substantial share of identified sales, increasing exposure to changes in their activity.", "recommended_action": "Protect key relationships and broaden acquisition/retention across the next customer-value tier.", "priority": "High" if top10_share >= 0.40 else "Medium"})
    if not countries.empty:
        leading_market = countries.sort_values("revenue_share", ascending=False).iloc[0]
        if leading_market.revenue_share >= 0.60:
            opportunities.append({"category": "Market revenue concentration", "evidence": f"{leading_market.country} contributes {leading_market.revenue_share:.1%} of eligible gross sales.", "business_meaning": "The business has substantial exposure to activity in its largest market.", "recommended_action": f"Protect service and retention in {leading_market.country} while validating a measured expansion test in adequately sized secondary markets.", "priority": "High"})
    opportunities_df = pd.DataFrame(opportunities, columns=["category", "evidence", "business_meaning", "recommended_action", "priority"])

    # Segment-level actions and observation-driven recommendations.
    seg = customers.groupby("cluster_id").agg(customers=("customer_key", "size"), revenue=("monetary", "sum"), avg_monetary=("monetary", "mean"), avg_frequency=("frequency", "mean"), avg_recency_days=("recency_days", "mean"), high_risk_share=("risk_score", lambda s: float((s >= 75).mean()))).reset_index()
    seg["segment"] = seg.cluster_id.map(name_by_cluster)
    recs=[]
    for r in seg.itertuples():
        if r.avg_recency_days >= customers.recency_days.median() and r.avg_monetary >= customers.monetary.median():
            action = "Test a tailored win-back message and measure recovered orders against a holdout."
            insight = "Above-median value with relatively long average recency."
            priority = "High"
        elif r.avg_frequency >= customers.frequency.quantile(.75):
            action = "Protect the repeat-purchase experience and test relevant complementary offers."
            insight = "Average order frequency is in the upper customer quartile."
            priority = "Medium"
        else:
            action = "Use low-cost onboarding or product discovery and monitor second-order conversion."
            insight = "The profile has lower purchase frequency or value than the portfolio center."
            priority = "Medium"
        recs.append({"segment": r.segment, "observation": f"{r.customers} customers; average value {r.avg_monetary:,.2f}, frequency {r.avg_frequency:.2f} orders, recency {r.avg_recency_days:.1f} days.", "insight": insight, "business_impact": f"This segment represents {r.revenue:,.2f} in identified eligible sales.", "recommended_action": action, "priority": priority})
    recommendations = pd.DataFrame(recs)

    # Cancellation/return summary distinguishes financial returns from unpriced quantity events.
    cancellation_summary = {
        "c_prefix_negative_quantity_price_positive_lines_after_dedup": int(totals.get("return_lines", 0)),
        "c_prefix_negative_quantity_signed_value_after_dedup": total_return,
        "c_prefix_negative_quantity_signed_units_after_dedup": int(totals.get("return_units_signed", 0)),
        "service_charge_revenue_after_dedup": service_gross,
        "service_cancellation_value_after_dedup": service_return,
        "service_cancellation_lines_after_dedup": int(totals.get("service_cancellation_lines", 0)),
        "non_sales_adjustment_value_before_dedup": float(totals.get("non_sales_adjustment_value_before_dedup", 0.0)),
        "gift_voucher_value_before_dedup": float(totals.get("gift_voucher_value_before_dedup", 0.0)),
        "negative_quantity_without_c_prefix_lines_before_dedup": int(totals["signals"].get("negative_quantity_without_c_prefix", 0)),
        "negative_quantity_zero_price_lines_before_dedup": int(totals["signals"].get("negative_quantity_zero_price", 0)),
        "c_prefix_nonnegative_quantity_rows_before_dedup": int(totals["signals"].get("c_prefix_nonnegative_quantity", 0)),
        "zero_price_positive_quantity_lines_before_dedup": int(totals["signals"].get("zero_price_rows", 0) - totals["signals"].get("negative_quantity_zero_price", 0)),
        "zero_price_positive_quantity_units_separate_before_dedup": zero_price_positive_quantity_units,
        "negative_price_rows_before_dedup": int(totals["signals"].get("negative_price_rows", 0)),
        "negative_price_bad_debt_adjustment_value_before_dedup": negative_price_adjustment_value,
        "c_prefix_nonnegative_quantity_signed_value_before_dedup": c_invoice_quantity_anomaly_value,
        "interpretation": "Only C-prefixed, negative-quantity, positive-price rows are included in signed monetary returns. Negative quantities without C prefixes (all observed at zero price) remain a separate unpriced adjustment/return signal. C-prefixed nonnegative-quantity and negative-price rows are anomalies, not ordinary sales or defined monetary returns. Positive-quantity zero-price lines are excluded from paid-sale units and revenue and reported separately. Negative-price rows are bad-debt adjustments in this workbook and are excluded from sales and returns.",
    }

    # Independent arithmetic checks over separately materialized analytical tables.
    invoice_revenue = float(invoice_table.order_value.sum())
    customer_revenue_check = float(customers.monetary.sum())
    daily_revenue_check = float(daily.sales_revenue.sum())
    expected_rfm = invoice_table.loc[invoice_table.customer_id.notna()].groupby("customer_id").agg(
        expected_monetary=("order_value", "sum"), expected_frequency=("invoice", "nunique"),
        expected_last_purchase=("order_date", "max"),
    )
    actual_rfm = customers.set_index("customer_id")
    rfm_reconciles = (
        len(expected_rfm) == len(actual_rfm)
        and expected_rfm.index.difference(actual_rfm.index).empty
        and np.allclose(expected_rfm.loc[actual_rfm.index, "expected_monetary"], actual_rfm.monetary)
        and np.array_equal(expected_rfm.loc[actual_rfm.index, "expected_frequency"].to_numpy(), actual_rfm.frequency.to_numpy())
        and bool((expected_rfm.loc[actual_rfm.index, "expected_last_purchase"].to_numpy() == actual_rfm.last_purchase.to_numpy()).all())
    )
    rfm_monetary_reconciles = len(expected_rfm) == len(actual_rfm) and expected_rfm.index.difference(actual_rfm.index).empty and np.allclose(expected_rfm.loc[actual_rfm.index, "expected_monetary"], actual_rfm.monetary)
    rfm_frequency_reconciles = len(expected_rfm) == len(actual_rfm) and expected_rfm.index.difference(actual_rfm.index).empty and np.array_equal(expected_rfm.loc[actual_rfm.index, "expected_frequency"].to_numpy(), actual_rfm.frequency.to_numpy())
    rfm_last_purchase_reconciles = len(expected_rfm) == len(actual_rfm) and expected_rfm.index.difference(actual_rfm.index).empty and bool((expected_rfm.loc[actual_rfm.index, "expected_last_purchase"].to_numpy() == actual_rfm.last_purchase.to_numpy()).all())
    checks = {
        "rows_raw": n_raw,
        "rows_after_exact_dedup": n_clean,
        "duplicate_occurrences_removed": n_raw - n_clean,
        "exact_duplicate_pattern_count": len(duplicate_multiplicity),
        "exact_duplicate_pattern_frequency_histogram": {str(k): int(v) for k, v in pd.Series(list(duplicate_multiplicity.values()), dtype="int64").value_counts().sort_index().items()},
        "max_exact_duplicate_multiplicity": max(duplicate_multiplicity.values(), default=1),
        "global_sales_invoices": total_orders,
        "gross_revenue_total": total_gross,
        "gross_revenue_invoice_aggregation": invoice_revenue,
        "gross_revenue_customer_aggregation": customer_revenue_check,
        "gross_revenue_daily_aggregation": daily_revenue_check,
        "signed_return_total": total_return,
        "units_total": total_units,
        "duplicate_removed_ordinary_sales_lines": duplicate_removed_sales_lines,
        "duplicate_removed_ordinary_sales_value": duplicate_removed_sales_value,
        "duplicate_removed_ordinary_sales_units": duplicate_removed_sales_units,
        "duplicate_affected_ordinary_sales_invoices": len(duplicate_affected_sales_invoices),
        "orders_before_exact_dedup": len(sale_invoices_before_dedup),
        "orders_after_exact_dedup": len(sale_invoices_after_dedup),
        "eligible_orders_removed_by_exact_dedup": len(sale_invoices_before_dedup - sale_invoices_after_dedup),
        "duplicate_removed_all_line_value": duplicate_removed_value,
        "positive_sale_units_invoice_aggregation": int(invoice_table.order_units.sum()),
        "positive_sale_units_daily_aggregation": int(daily.sales_units.sum()),
        "positive_sale_units_reconcile": bool(total_units == int(invoice_table.order_units.sum()) == int(daily.sales_units.sum())),
        "row_type_count_total": int(sum(sum(s["record_type_counts"].values()) for s in totals["sheets"].values())),
        "row_type_counts_reconcile": bool(sum(sum(s["record_type_counts"].values()) for s in totals["sheets"].values()) == n_raw),
        "date_min": str(min_date),
        "date_max": str(max_date),
        "rfm_reference_date": str(reference_date.date()),
        "raw_columns": raw_columns,
        "observed_dtypes": type_report,
        "missing_values_by_column": raw_missing,
        "distinct_invoices": int(invoice_table.invoice.nunique()),
        "distinct_customers_on_sales": total_customers,
        "distinct_products_on_sales": int(products.stock_code.nunique()),
        "distinct_countries_on_sales": int(countries.country.nunique()),
        "high_volume_single_order_products": products.loc[products.unusual_volume_flag, ["stock_code", "product_description", "sales_units", "sales_orders", "sales_revenue", "returns_units", "return_line_rate"]].head(20).to_dict("records"),
        "identified_customer_revenue_reconciles_rfm": bool(np.isclose(identified_customer_sale_value, customer_revenue_check)),
        "rfm_reconciles_independent_order_customer_aggregation": bool(rfm_reconciles),
        "rfm_monetary_reconciles": bool(rfm_monetary_reconciles),
        "rfm_frequency_reconciles": bool(rfm_frequency_reconciles),
        "rfm_last_purchase_reconciles": bool(rfm_last_purchase_reconciles),
        "orders_kpi_formula_reconciles": bool(total_orders == int(invoice_table.invoice.nunique())),
        "customers_kpi_formula_reconciles": bool(total_customers == int(invoice_table.customer_id.nunique())),
        "aov_kpi_formula_reconciles": bool(np.isclose(kpis["average_order_value"], invoice_revenue / total_orders)),
        "repeat_rate_kpi_formula_reconciles": bool(np.isclose(kpis["repeat_customer_rate"], customers.frequency.gt(1).sum() / total_customers)),
        "return_rate_kpi_formula_reconciles": bool(np.isclose(kpis["c_invoice_return_line_rate"], totals["return_lines"] / totals["ordinary_sale_lines"])),
        "gross_revenue_reconciles_invoice_and_daily": bool(np.isclose(total_gross, invoice_revenue) and np.isclose(total_gross, daily_revenue_check)),
        "customer_revenue_coverage_reconciles": bool(np.isclose(identified_customer_sale_value + missing_customer_sale_value, total_gross)),
        "clustering": {"selected_k": SELECTED_K, "transform": "log1p(recency days), log1p(order frequency), log1p(monetary); StandardScaler", "seed": SEED, "evaluation_k": [int(x) for x in evaluation.k]},
        "cluster_profiles_reconcile": bool(int(profiles.customers.sum()) == len(customers) and np.isclose(profiles.revenue.sum(), customers.monetary.sum())),
        "risk_formula_reconciles": bool(np.allclose(customers.risk_score, 100 * (0.50 * customers.recency_percentile + 0.30 * customers.activity_decline + 0.20 * customers.return_value_ratio))),
        "all_cluster_scores_finite": bool(np.isfinite(evaluation[["silhouette_sampled", "seed_stability_ari", "inertia"]].to_numpy()).all()),
        "versions": {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__, "scikit_learn": sklearn.__version__, "pyarrow": pyarrow.__version__},
        "source_sha256": hashlib.sha256(WORKBOOK.read_bytes()).hexdigest(),
    }
    if not checks["gross_revenue_reconciles_invoice_and_daily"] or not checks["identified_customer_revenue_reconciles_rfm"] or not checks["rfm_reconciles_independent_order_customer_aggregation"]:
        raise AssertionError(f"Independent reconciliation failed: gross={checks['gross_revenue_reconciles_invoice_and_daily']}, identified={checks['identified_customer_revenue_reconciles_rfm']}, RFM={checks['rfm_monetary_reconciles']}/{checks['rfm_frequency_reconciles']}/{checks['rfm_last_purchase_reconciles']}")
    if not checks["customer_revenue_coverage_reconciles"]:
        raise AssertionError("Customer ID revenue coverage does not reconcile to gross sales")
    if not checks["row_type_counts_reconcile"] or not checks["cluster_profiles_reconcile"] or not checks["risk_formula_reconciles"] or not checks["all_cluster_scores_finite"]:
        raise AssertionError("Row categories, cluster profiles, risk rules or clustering evaluation failed validation")
    if not all(checks[k] for k in ["orders_kpi_formula_reconciles", "customers_kpi_formula_reconciles", "aov_kpi_formula_reconciles", "repeat_rate_kpi_formula_reconciles", "return_rate_kpi_formula_reconciles"]):
        raise AssertionError("One or more KPI formula validations failed")
    if not checks["positive_sale_units_reconcile"]:
        raise AssertionError("Positive-price sale units failed independent table reconciliation")
    if checks["eligible_orders_removed_by_exact_dedup"] != 0:
        raise AssertionError("Deduplication unexpectedly removed an eligible sales invoice")
    if not np.isfinite(x_scaled).all() or not np.isfinite(customers[["recency_days", "frequency", "monetary", "risk_score"]].to_numpy()).all():
        raise AssertionError("NaN or infinite values found in RFM/risk calculations")

    # Save processed, dashboard-ready outputs only.
    rfm = customers.rename(columns={"recency_days": "recency", "frequency": "frequency_orders", "monetary": "monetary_value"})
    rfm = rfm[["customer_key", "recency", "frequency_orders", "monetary_value", "first_purchase", "last_purchase", "cluster_id", "risk_score", "risk_level", "activity_decline", "return_value", "return_lines"]]
    write_table(daily, "time_series_daily")
    write_table(monthly.drop(columns=["month_dt"]), "time_series_monthly")
    write_table(rfm, "customer_rfm")
    write_table(customer_segment, "customer_segments")
    write_table(evaluation, "clustering_evaluation")
    write_table(profiles, "cluster_profiles")
    write_table(products, "product_metrics")
    write_table(product_month.drop(columns=["month_dt"]), "product_monthly_metrics")
    write_table(countries, "country_metrics")
    write_table(country_month.drop(columns=["month_dt"]), "country_monthly_metrics")
    write_table(returns_product, "return_product_metrics")
    write_table(returns_country, "return_country_metrics")
    write_table(risk, "customer_risk")
    write_table(opportunities_df, "opportunities")
    write_table(recommendations, "recommendations")
    write_table(invoice_table.drop(columns=["customer_id"]), "order_metrics")
    save_json(kpis, "kpi_summary.json")
    save_json(cancellation_summary, "cancellation_return_summary.json")
    save_json(checks, "validation_report.json")

    print("\n=== KPI SUMMARY ===")
    for key in ["gross_positive_sales_value", "signed_cancellation_return_value", "net_sales_after_defined_returns", "orders", "customers", "units", "average_order_value", "revenue_per_identified_customer", "c_invoice_return_line_rate", "repeat_customer_rate", "revenue_growth_2011_jan_nov_vs_2010_jan_nov", "top_10_identified_customer_revenue_share", "identified_customer_revenue", "unidentified_customer_revenue"]:
        print(f"{key}: {kpis.get(key)}")
    print("\n=== DUPLICATE IMPACT ===")
    for key in ["rows_raw", "rows_after_exact_dedup", "duplicate_occurrences_removed", "duplicate_removed_ordinary_sales_lines", "duplicate_removed_ordinary_sales_value", "duplicate_removed_ordinary_sales_units", "duplicate_affected_ordinary_sales_invoices"]:
        print(f"{key}: {checks.get(key)}")
    print("\n=== CANCELLATION / DATA SIGNALS ===")
    print(json.dumps({**cancellation_summary, **totals["signals"]}, indent=2))
    print("\n=== CLUSTER EVALUATION ===")
    print(evaluation.to_string(index=False))
    print("\n=== CLUSTER PROFILES (inspect before segment naming) ===")
    print(profiles.to_string(index=False))
    print("\n=== VALIDATION ===")
    print(json.dumps(checks, indent=2, default=str))
    print(f"\nWrote analytical outputs to {OUT}")


if __name__ == "__main__":
    main()
