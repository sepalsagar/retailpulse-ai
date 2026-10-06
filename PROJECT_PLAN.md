# RetailPulse AI — Project Plan and Phase 2 Findings

**Project:** Retail Revenue & Customer Intelligence System  
**Context:** AICTE | IBM SkillsBuild Data Analytics with AI Internship 2026  
**Status:** Phase 1 and Phase 2 are complete. The Phase 3 local dashboard is implemented and ready for visual review; README finalization and the project report have not started.

## 1. Business problem

The retailer has more than one million historical transaction lines, but needs a clear way to understand revenue performance, customer value and inactivity, products, markets, and cancellation-related exposure. The project will turn transaction data into evidence-backed management decisions.

## 2. Business objective

Build a student-friendly business intelligence application that describes what is happening, investigates likely drivers and risks, identifies opportunities, and recommends practical actions. Every displayed conclusion should be traceable to a defined calculation from the supplied data.

## 3. Dataset description and inspection findings

- **Source expected by the brief:** UCI Online Retail II, [official dataset page](https://archive.ics.uci.edu/dataset/502/online%2Bretail%2Bii).
- **Local workbook inspected:** `data/online_retail_II.xlsx` (45,622,278 bytes).
- Workbook contains two sheets: `Year 2009-2010` (525,461 rows) and `Year 2010-2011` (541,910 rows), for **1,067,371 transaction rows** total.
- Both sheets have the same eight fields: `Invoice`, `StockCode`, `Description`, `Quantity`, `InvoiceDate`, `Price`, `Customer ID`, and `Country`. Observed dtypes are object/text for invoice, stock code, description and country; integer quantity; datetime invoice date; float price and customer ID.
- The data covers **2009-12-01 07:45 through 2011-12-09 12:50**. The sheets' date ranges overlap from 2010-12-01 through 2010-12-09, so sheet labels must not be treated as disjoint time partitions.
- Across the workbook there are **53,628 distinct invoice identifiers, 5,305 stock codes, 5,942 non-missing customer IDs, and 43 countries**. These counts are distinct across both sheets, not sums of each sheet's distinct counts.
- Reading one sheet at a time with pandas used about **123 MB** and **126 MB** for the respective in-memory frames. The 45.6 MB compressed workbook therefore expands substantially; process one sheet at a time and avoid holding multiple raw copies.

## 4. Data-quality findings and proposed handling

| Check | Observed | Planning decision |
|---|---:|---|
| Missing `Customer ID` | 243,007 rows (22.8%) | Keep rows for transaction/product/market analysis where usable; exclude from customer-level RFM and segmentation, and report coverage. |
| Missing `Description` | 4,382 rows (0.4%) | Retain stock-code rows; display a clear unknown/missing description label where needed. |
| Exact repeated rows | 34,335 repeated row occurrences across the concatenated workbook by 64-bit full-row fingerprint; 6,865 repeats within the first sheet and 5,268 within the second (within-sheet and cross-sheet categories overlap). | Confirm/deduplicate exact repeated transaction lines in analytical sales tables and disclose the count. Fingerprint count has a theoretical hash-collision caveat; use direct duplicate checks in the actual preprocessing. |
| Negative quantity | 22,950 rows; 3,457 do not have a `C`-prefixed invoice | Treat negative quantities as return/cancellation candidates, but preserve and analyze them separately; inspect invoice-prefix and quantity signals together. |
| `C`-prefixed invoice | 19,494 rows; one has a positive quantity | Use the prefix as a cancellation signal, not as the sole definition. Resolve row-level overlaps and anomalies explicitly. |
| Zero quantity | 0 rows | No action currently needed. |
| Zero price | 6,202 rows | Do not count as positive-price revenue; retain for units/transaction counts only where appropriate and disclose. These may include free items. |
| Negative price | 5 rows | Exclude from ordinary sales revenue pending transparent treatment; inspect as anomalous adjustment records. |
| Date parsing | No parse failures in either sheet | Preserve invoice timestamps and derive calendar fields after cleaning. |
| Strong market skew | United Kingdom accounts for 485,852 rows in the first sheet and 495,478 in the second | Show market size alongside rates and apply minimum-volume thresholds to comparisons. |

The workbook includes 12,326 and 10,624 negative-quantity rows in the respective sheets. Negative quantities and cancellation invoice codes are not perfectly aligned, confirming that cancellations/returns must be represented using multiple signals rather than silently filtered. For an initial gross-sales baseline, rows with positive quantity, positive price, and no `C` invoice prefix number 1,041,670; this is a proposed analytical subset, **not** a final cleaned-row count or a net revenue result. Final rules should be validated during Phase 2.

## 5. Proposed KPIs

Use a documented definition for each metric and expose its scope (gross sales, returns, or net where defensible):

1. **Revenue:** sum of `Quantity × Price` for eligible positive-sale lines; separately report return/cancellation value and do not imply it is automatically lost revenue.
2. **Orders:** distinct valid, non-cancellation invoice identifiers in the selected period and filters.
3. **Customers:** distinct non-missing customer IDs among eligible sales; show ID coverage.
4. **Units sold:** sum of positive quantities on eligible sale lines; keep returned units separate.
5. **Average order value:** eligible sales revenue divided by eligible distinct orders.
6. **Average revenue per customer:** eligible sales revenue divided by identified customers, with customer coverage stated.
7. **Cancellation rate:** cancellation/return invoice count or line count divided by the corresponding sales invoice/line denominator; choose and label one clear definition after validating invoice behavior.
8. **Repeat customer rate:** identified customers with more than one eligible distinct order divided by identified customers.
9. **Revenue growth:** period-over-period comparison only for comparable complete periods; show the selected comparison window.
10. **Customer revenue concentration:** share of identified revenue contributed by top customers and cumulative contribution, with the revenue scope stated.

## 6. Analytical questions

- What are the sales, order, customer and unit trends across comparable periods?
- Which products and countries contribute the most revenue and how concentrated is performance?
- How much of customer revenue is attributable to repeat buyers and high-value customers?
- Which customer groups show long recency or falling purchasing activity?
- Where are return/cancellation signals concentrated, and are patterns large enough to merit action?
- Which evidence-backed actions can improve retention, product focus, or market development?

## 7. Customer analysis approach

Build customer metrics only from rows with a usable customer ID and eligible sale definition. RFM will use a fixed analysis date just after the dataset's latest valid transaction; recency is days since the last eligible purchase, frequency is distinct eligible invoices, and monetary is eligible sales value. Standardize skewed customer features (consider log transforms) before clustering. Compare a small set of cluster counts using silhouette score plus profile stability and business interpretability; document the chosen method and limitations. Name segments only after reviewing measured profiles. Keep customer IDs out of unnecessary UI exposure; use masked or anonymous identifiers in prioritized tables.

## 8. Product analysis approach

Aggregate eligible revenue and units by stock code, joining descriptions carefully because names may be missing or vary. Compare revenue, units, trend, and cancellation/return signals. Use cumulative revenue contribution/Pareto analysis to assess concentration. Define “declining” only by comparing equivalent periods and “unusual” only relative to a stated peer or historical baseline. Do not call low absolute revenue underperformance without context.

## 9. Market analysis approach

Aggregate revenue, orders, identified customers, and AOV by country. Present market size with rates; suppress or flag low-volume comparisons. Calculate growth only across complete, comparable periods. Highlight strong markets, meaningful growth, and declining activity only when the sample supports the claim.

## 10. Risk analysis

Analyze cancellation candidates separately using invoice prefix, quantity sign, and price sign. Track line/invoice volumes and values without assuming every return is lost revenue. Build a transparent **Customer Risk Indicator**, not a churn prediction: combine recency relative to the dataset/customer distribution, changes in purchase activity where periods support comparison, historical monetary value, and cancellation behavior. Document thresholds and show prioritized, privacy-conscious actions.

## 11. Opportunity analysis

Use explicit rules tied to calculated evidence. Candidate categories are high-value inactive customers; growing markets with adequate volume; products with strong contribution and increasing demand; concentration reduction; and high-value markets with low relative reach. Each opportunity should state evidence, why it matters, action, and priority. Cross-sell analysis is conditional on basket support and should be omitted if the data does not support reliable associations.

## 12. Machine-learning approach

Use unsupervised customer clustering on standardized customer-level RFM features if customer ID coverage and row counts remain adequate after cleaning. Evaluate multiple cluster counts with silhouette and profile interpretability; do not claim predictive churn performance. No supervised churn label is assumed to exist. The risk indicator remains rule-based and separate from clustering.

## 13. Dashboard structure

Planned Streamlit navigation:

1. **Executive Overview:** KPI cards, revenue/order trends, leading products and countries, and calculated fact → interpretation → implication insights.
2. **Customer Intelligence:** RFM profiles, segments, segment value/size/recency/frequency, and privacy-conscious risk prioritization.
3. **Product & Market Intelligence:** product and country comparisons, contribution, trends, and sample-size context.
4. **Risks & Opportunities:** cancellation patterns, concentration risks, rule-based opportunities, and prioritized actions.

Use Plotly charts only where they answer a business question. Keep filters limited to useful date, country, product, and segment selections, and design for empty or narrow selections.

## 14. Technology stack

- Python, pandas, and openpyxl for workbook inspection and data preparation.
- Streamlit for the application and Plotly for interactive charts.
- scikit-learn for the evaluated RFM clustering (Phase 2 confirmed K-Means).
- PyArrow for compact Parquet analytical outputs.
- A compact processed-data/aggregate cache may be generated locally to avoid repeatedly reading Excel. Generated data and the original workbook should not be committed.
- Project-local virtual environment: `.venv`. Phase 2 outputs were generated and validated with pandas 3.0.6 (recorded in `validation_report.json`). For the Streamlit 1.54 runtime, the shared environment now uses pandas 2.3.3 because Streamlit requires pandas below 3; openpyxl 3.1.5, scikit-learn 1.9.1, PyArrow 25.0.1, and Plotly 6.5.2 are pinned alongside Streamlit in `requirements.txt`.

## 15. Testing plan

At the Phase 2 checkpoint, row counts and exclusions were reconciled and KPI formulas, RFM values, cluster profiles/stability, and risk rules were independently verified. Dashboard pages, filters, charts, application startup, README commands, and report values were subsequently reviewed in the later phases.

## 16. Documentation plan

Keep `README.md`, the application, and `PROJECT_REPORT.docx` aligned with implemented calculations. Attribute the UCI dataset and instruct users to download it separately to `data/online_retail_II.xlsx`. The final report will use actual findings and screenshots from the finished application; no results or screenshots will be fabricated. Phase 1 established the plan; Phase 2 appended observed data decisions and analytical results.

---

## Phase 2 — Final data-preparation and analytical decisions

This section supersedes the preliminary cleaning and KPI decisions above where they differ. The raw workbook was left unchanged. The reproducible pipeline is `analysis.py`; run it from the repository root with `.venv\\Scripts\\python.exe analysis.py`. It reads one workbook sheet at a time and writes compact Parquet/JSON outputs to the ignored `data/processed/` directory. The 19 generated outputs total about 2.1 MB; raw line-level data is not exported.

### Final duplicate treatment

- The workbook has **34,335 repeated full-row occurrences** in **32,907 repeated row patterns**; most patterns occur twice, with a maximum multiplicity of 20. The repeated rows are identical across invoice, stock code, description, quantity, timestamp, price, customer ID, and country. All are necessarily within the same invoice/product transaction signature.
- Direct pandas duplicate checks are used within a sheet; 64-bit row fingerprints identify repeats across the two sheets. A hash collision is theoretically possible but extremely unlikely. The original rows are not edited; repeated rows are excluded only from analytical aggregates.
- Deduplication removes **33,663 eligible merchandise-sale lines**, reducing eligible gross merchandise value by **£466,309.43** and merchandise units by **214,609**. Those duplicate sale lines occur in 5,079 invoices, but **no eligible sales invoice is removed** because one copy of each repeated line remains. The value change is material (about 2.4% of gross merchandise sales), so the dashboard/report must disclose the choice and its effect.
- Intra-sheet and cross-sheet repeats are deduplicated together because exact full-row repeats are redundant records for the transaction-level metrics. Duplicate evidence remains in `validation_report.json`.

### Final transaction classification and cleaning rules

1. **Merchandise sale line:** numeric invoice ID; not C-prefixed; a merchandise stock code; positive quantity; positive unit price; and not an exact duplicate. Product descriptions and stock-code exceptions are used to separate service, adjustment, and voucher lines.
2. **Merchandise cancellation/return line:** C-prefixed invoice; negative quantity; positive unit price; merchandise stock code; and not an exact duplicate. The negative signed line value is reported separately and is not silently removed from the raw data.
3. **Service charges:** postage, dot-com postage, and carriage codes/descriptions are aggregated separately from merchandise. Their defined C-prefixed negative-quantity reversals are also reported separately.
4. **Adjustments:** manual, bad-debt, discount, sample, bank-charge, Amazon-fee, commission, explicit adjustment/test codes, and A-prefixed accounting entries are not classified as product sales or merchandise returns. The five negative-price records are labeled “Adjust bad debt”; their signed value is **−£158,676.14**. An A-prefixed positive bad-debt counterpart also exists, so accounting entries are kept out of product revenue rather than netted into retail sales.
5. **Gift vouchers:** treated separately from merchandise revenue.
6. **Zero-price lines:** 6,202 total. Of these, 2,745 have positive quantity (252,259 units) and are reported separately, not counted as paid-sale revenue or merchandise units. The other 3,457 have negative quantity and are unpriced return/adjustment signals.
7. **Uncoded negative quantities:** 3,457 negative-quantity rows do not have a C prefix; all have zero price in this workbook. They are retained as an unpriced adjustment/return signal and excluded from monetary return calculations.
8. **Other fields:** 243,007 rows lack Customer ID; retain them for non-customer aggregates, exclude them from RFM/clustering, and show coverage. Retain stock-code rows with missing descriptions under a fallback label. Dates parsed without failures. The two sheet date ranges overlap; all periods are based on actual invoice dates.

High quantities are not automatically discarded: for example, stock code 23843 has an 80,995-unit sale and a matching cancellation, so the unusual volume and return are flagged for review. Product-level flags also identify high-unit volume concentrated in very few orders.

### Final revenue definitions and KPI formulas

All monetary values are **GBP (£)**, per UCI dataset metadata. The headline revenue is gross merchandise revenue; postage/carriage is shown as a separate service measure. No value is called “net revenue” unless the defined return amount is subtracted.

| KPI | Final formula / scope | Observed result |
|---|---|---:|
| Gross merchandise revenue | Sum of `Quantity × Price` for eligible merchandise sale lines after exact-row deduplication | £19,640,817.62 |
| Merchandise cancellation/return value | Signed sum of defined C-prefixed merchandise return lines | −£716,394.37 |
| Net merchandise sales | Gross merchandise revenue + defined signed merchandise return value | £18,924,423.25 |
| Service charges | Eligible numeric-invoice postage/carriage lines, reported separately | £452,006.55 |
| Service cancellation value | Signed eligible C-prefixed service reversals | −£15,620.22 |
| Orders | Distinct invoices with at least one eligible merchandise sale line | 39,516 |
| Customers | Distinct non-missing Customer IDs among eligible merchandise sale orders | 5,852 |
| Paid merchandise units | Sum of positive quantities on eligible merchandise sale lines; zero-price quantities separate | 11,187,743 |
| AOV | Gross merchandise revenue ÷ eligible distinct orders | £497.03 |
| Revenue per identified customer | Identified-customer gross merchandise revenue ÷ eligible identified customers | £2,916.25 |
| Cancellation/return line rate | Defined merchandise return lines ÷ eligible merchandise sale lines | 1.785% |
| Repeat customer rate | Identified customers with more than one distinct eligible order ÷ identified eligible customers | 72.35% |
| Revenue growth | (Jan–Nov 2011 merchandise revenue ÷ Jan–Nov 2010 merchandise revenue) − 1; both complete 11-month calendar windows | +2.98% |
| Customer revenue concentration | Top 10 identified customers’ merchandise revenue ÷ all identified-customer merchandise revenue | 16.14% |

Gross transaction value including service charges is £20,092,824.17; net transaction value after the defined merchandise and service reversals is £19,360,809.58. These supporting totals do not replace the merchandise revenue KPI.

**Customer coverage:** 824,364 raw transaction rows have a Customer ID and 243,007 do not. Among deduplicated eligible merchandise sale lines, 776,420 have an ID and 226,728 do not. Identified customers account for **86.89%** of eligible merchandise revenue (£17,065,908.67); **13.11%** (£2,574,908.95) has no customer ID. Customer findings therefore describe identified transactions only.

### Comparable periods

The sheets are not treated as separate periods. Revenue growth compares **January–November 2011 with January–November 2010**, based on actual invoice dates. December is omitted because 2011 ends on December 9. Country and product opportunities require minimum volume in both comparison windows.

### RFM and customer risk

- RFM uses identified customers and eligible merchandise sale lines. **Recency** is days since the last eligible purchase; **frequency** is distinct eligible merchandise invoices; **monetary** is eligible gross merchandise value. Reference date is **2011-12-10**, the day after the latest valid merchandise sale (2011-12-09).
- Customer outputs use generated `customer_key` values rather than raw Customer IDs. Missing-ID transactions remain in overall aggregates but never enter RFM or clusters.
- The separate **Customer Risk Indicator** is rule-based, not churn prediction: score = 50% recency percentile + 30% six-month spending decline + 20% defined return-value ratio. Recent spending uses Jun–Nov 2011 and the preceding window uses Dec 2010–May 2011. Risk levels are Low (≤50), Moderate (>50–75), High (>75); high-value at-risk opportunities require top-quartile monetary value and score ≥60.

### Customer clustering decision

RFM monetary, frequency, and recency features are `log1p` transformed and standardized. K-Means was evaluated for K=2–6 with sampled silhouette and a second-seed adjusted Rand comparison. K=2 has the best silhouette (0.441), but combines groups that support different actions. **K=4** was selected because its profiles separate recent developing, valuable but inactive, low-value inactive, and high-value active customers while remaining stable across seeds (ARI 0.997; silhouette 0.366). Cluster names were assigned only after inspecting the profiles:

| Segment | Customers | Avg. monetary | Avg. orders | Avg. recency | Revenue share |
|---|---:|---:|---:|---:|---:|
| Low-Value Inactive | 1,954 | £315.97 | 1.38 | 395 days | 3.62% |
| Recent Developing | 1,260 | £837.37 | 3.02 | 29 days | 6.18% |
| Valuable At Risk | 1,448 | £1,967.90 | 5.06 | 229 days | 16.70% |
| High-Value Active | 1,190 | £10,541.10 | 19.14 | 28 days | 73.50% |

No supervised churn label is available or claimed.

### Product, market, risk, and opportunity rules/results

- Product metrics use merchandise only, with revenue, units, distinct orders, identified customers, monthly trend, contribution/Pareto share, and separate return indicators. Service and adjustment stock codes remain classed separately. Products are not called underperforming from low absolute revenue.
- Country metrics use merchandise revenue, distinct orders/customers, AOV, comparable-period growth, and cancellation indicators. Growth opportunities require at least 100 orders in each Jan–Nov window; product growth opportunities require at least 20 orders in each window and at least 20% growth.
- The UK contributes **85.5%** of eligible gross merchandise sales, triggering a market-concentration opportunity. France grew **40.3%** in the comparable windows (203 orders in Jan–Nov 2010 and 345 in Jan–Nov 2011). Small-sample markets are not promoted by the growth rule.
- The rule-based opportunity engine found 154 high-value at-risk customers with £1,018,708.34 of historical eligible sales; a UK market-concentration opportunity; one qualifying growing market (France); and the top 10 qualifying merchandise products. Product opportunities exclude services and adjustments.
- Cancellation risk is separated from gross sales. Defined merchandise returns total 17,910 lines, −467,731 units, and −£716,394.37 after deduplication. Service reversals total 243 lines and −£15,620.22. The dashboard must keep the unpriced return signals and accounting adjustments visible as separate data-quality indicators.

### Validation and outputs

The analysis script verifies row-category reconciliation; invoice, daily, and customer revenue totals; sales units; identified/unidentified revenue coverage; orders, customers, AOV, repeat rate, and return-rate formulas; independent RFM monetary/frequency/last-purchase values; cluster profiles and seed stability; risk score formula; and NaN/inf checks for RFM and risk inputs. These checks passed. The source workbook SHA-256 is recorded in `data/processed/validation_report.json` to detect accidental changes.

Phase 2 files created: `analysis.py`, `requirements.txt`, `.gitignore`, and `data/processed/` aggregates. At the Phase 2 checkpoint, dashboard and submission documentation remained for later phases.


## Phase 4 - Documentation and report

Phase 4 created `PROJECT_REPORT.docx` and finalized `README.md` from the implemented application, validated Phase 2 outputs, and existing dashboard screenshots. `generate_report.py` recreates the report from those outputs and screenshots; `python-docx` is pinned for this purpose. The validated analytical logic and processed outputs were not changed. The report and README state the implemented methodology, historical results, assumptions, and limitations; the four dashboard screenshots were verified as existing files.
