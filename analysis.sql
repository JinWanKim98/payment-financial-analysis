-- SQL sections used by the analysis notebook.
-- Each section is selected by filename; do not execute the whole file as one batch.

-- BEGIN FILE: 01_schema.sql
-- SQLite >=3.37. Each connection must enable foreign_keys.
CREATE TABLE dim_company (
  company TEXT PRIMARY KEY, name TEXT NOT NULL, cik TEXT NOT NULL UNIQUE,
  fiscal_year_end TEXT NOT NULL
) STRICT;
CREATE TABLE dim_period (
  period_id TEXT PRIMARY KEY, company TEXT NOT NULL REFERENCES dim_company,
  fiscal_year INTEGER NOT NULL CHECK(fiscal_year BETWEEN 2021 AND 2025),
  period_type TEXT NOT NULL CHECK(period_type IN ('duration','instant')),
  period_start TEXT, period_end TEXT NOT NULL,
  CHECK((period_type='instant' AND period_start IS NULL) OR
        (period_type='duration' AND period_start IS NOT NULL AND period_start<period_end)),
  UNIQUE(company,fiscal_year,period_type), UNIQUE(period_id,company,period_type)
) STRICT;
CREATE TABLE dim_item (
  item_id TEXT PRIMARY KEY, statement TEXT NOT NULL,
  period_type TEXT NOT NULL CHECK(period_type IN ('duration','instant')),
  definition_json TEXT NOT NULL, UNIQUE(item_id,period_type)
) STRICT;
CREATE TABLE dim_filing (
  accession TEXT PRIMARY KEY, company TEXT NOT NULL REFERENCES dim_company,
  form TEXT NOT NULL CHECK(form IN ('10-K','10-K/A','10-Q','10-Q/A')),
  publication_fiscal_year INTEGER, report_period_end TEXT NOT NULL,
  filed_date TEXT NOT NULL, sec_url TEXT NOT NULL,
  CHECK(form NOT IN ('10-K','10-K/A') OR publication_fiscal_year IS NOT NULL),
  UNIQUE(accession,company)
) STRICT;
CREATE TABLE dim_source (
  source_id TEXT PRIMARY KEY, company TEXT NOT NULL REFERENCES dim_company,
  kind TEXT NOT NULL CHECK(kind IN ('submissions','companyfacts','issuer_annual_pdf')),
  path TEXT NOT NULL UNIQUE, url TEXT NOT NULL, sha256 TEXT NOT NULL CHECK(length(sha256)=64),
  retrieved_at_utc TEXT NOT NULL, UNIQUE(source_id,company,kind)
) STRICT;
CREATE TABLE model_policy (
  company TEXT PRIMARY KEY REFERENCES dim_company, anchor_accession TEXT NOT NULL,
  anchor_fiscal_year INTEGER NOT NULL, anchor_period_end TEXT NOT NULL,
  filed_cutoff TEXT NOT NULL, source_snapshot TEXT NOT NULL,
  FOREIGN KEY(anchor_accession,company) REFERENCES dim_filing(accession,company)
) STRICT;
CREATE TABLE fact_observation (
  observation_id TEXT PRIMARY KEY, company TEXT NOT NULL,
  period_id TEXT NOT NULL, period_type TEXT NOT NULL,
  item_id TEXT NOT NULL, accession TEXT NOT NULL,
  unit TEXT NOT NULL CHECK(unit='USD'), value_usd INTEGER NOT NULL,
  taxonomy TEXT NOT NULL CHECK(taxonomy='us-gaap'), api_tag TEXT NOT NULL,
  api_source_id TEXT NOT NULL, api_source_kind TEXT NOT NULL DEFAULT 'companyfacts' CHECK(api_source_kind='companyfacts'),
  UNIQUE(company,period_id,item_id,accession),
  UNIQUE(observation_id,company,value_usd),
  FOREIGN KEY(period_id,company,period_type) REFERENCES dim_period(period_id,company,period_type),
  FOREIGN KEY(item_id,period_type) REFERENCES dim_item(item_id,period_type),
  FOREIGN KEY(accession,company) REFERENCES dim_filing(accession,company),
  FOREIGN KEY(api_source_id,company,api_source_kind) REFERENCES dim_source(source_id,company,kind)
) STRICT;
CREATE TABLE observation_api_pointer (
  observation_id TEXT NOT NULL REFERENCES fact_observation,
  json_pointer TEXT NOT NULL, PRIMARY KEY(observation_id,json_pointer)
) STRICT;
CREATE TABLE pdf_evidence (
  observation_id TEXT PRIMARY KEY, company TEXT NOT NULL, canonical_amount_usd INTEGER NOT NULL,
  pdf_source_id TEXT NOT NULL, pdf_source_kind TEXT NOT NULL DEFAULT 'issuer_annual_pdf' CHECK(pdf_source_kind='issuer_annual_pdf'),
  pdf_page INTEGER NOT NULL CHECK(pdf_page>0), source_token TEXT NOT NULL,
  source_signed_amount_usd INTEGER NOT NULL, normalization TEXT NOT NULL,
  source_label TEXT NOT NULL, raw_source_label TEXT NOT NULL, comparability_note TEXT NOT NULL,
  CHECK((normalization='signed' AND canonical_amount_usd=source_signed_amount_usd) OR
    (normalization='payment_magnitude' AND source_signed_amount_usd<=0 AND canonical_amount_usd=-source_signed_amount_usd)),
  FOREIGN KEY(observation_id,company,canonical_amount_usd) REFERENCES fact_observation(observation_id,company,value_usd),
  FOREIGN KEY(pdf_source_id,company,pdf_source_kind) REFERENCES dim_source(source_id,company,kind)
) STRICT;
CREATE TABLE dim_kpi (
  metric_id TEXT PRIMARY KEY, label_en TEXT NOT NULL, unit TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ready','deferred_source_mapping')),
  period_rule TEXT NOT NULL, definition_json TEXT NOT NULL
) STRICT;
CREATE TABLE bridge_kpi_input (
  metric_id TEXT NOT NULL REFERENCES dim_kpi, company TEXT NOT NULL REFERENCES dim_company,
  item_id TEXT NOT NULL REFERENCES dim_item, year_offset INTEGER NOT NULL CHECK(year_offset IN (-1,0)),
  PRIMARY KEY(metric_id,company,item_id,year_offset)
) STRICT;
CREATE TABLE model_input (
  path TEXT PRIMARY KEY, sha256 TEXT NOT NULL CHECK(length(sha256)=64)
) STRICT;
CREATE INDEX ix_observation_grain ON fact_observation(company,period_id,item_id);
CREATE INDEX ix_filing_eligibility ON dim_filing(company,filed_date,form);

-- BEGIN FILE: 02_reporting_views.sql
-- All versions remain visible. Optional quarters and after-cutoff publications
-- never enter annual selection. Tied latest dates are ambiguous, not arbitrary.
CREATE VIEW v_eligible_observations AS
SELECT o.*,f.filed_date,f.publication_fiscal_year,f.sec_url
FROM fact_observation o JOIN dim_filing f USING(accession,company)
JOIN model_policy p USING(company)
WHERE f.form IN ('10-K','10-K/A') AND f.filed_date<=p.filed_cutoff
  AND f.publication_fiscal_year<=p.anchor_fiscal_year
  AND f.report_period_end<=p.anchor_period_end;

CREATE VIEW v_latest_api_candidate AS
WITH dated AS (
 SELECT *,MAX(filed_date) OVER(PARTITION BY company,period_id,item_id) AS latest_date
 FROM v_eligible_observations
)
SELECT company,period_id,item_id,COUNT(*) AS candidate_count,
 CASE WHEN COUNT(*)=1 THEN MIN(observation_id) END AS observation_id,
 CASE WHEN COUNT(*)=1 THEN MIN(value_usd) END AS value_usd,
 MAX(filed_date) AS filed_date
FROM dated WHERE filed_date=latest_date GROUP BY company,period_id,item_id;

CREATE VIEW v_reviewed_baseline_candidate AS
WITH dated AS (
 SELECT o.*,MAX(filed_date) OVER(PARTITION BY company,period_id,item_id) AS latest_date
 FROM v_eligible_observations o
 WHERE EXISTS(SELECT 1 FROM pdf_evidence e WHERE e.observation_id=o.observation_id)
)
SELECT company,period_id,item_id,COUNT(*) AS candidate_count,
 CASE WHEN COUNT(*)=1 THEN MIN(observation_id) END AS observation_id,
 CASE WHEN COUNT(*)=1 THEN MIN(value_usd) END AS value_usd,
 MAX(filed_date) AS filed_date
FROM dated WHERE filed_date=latest_date GROUP BY company,period_id,item_id;

CREATE VIEW v_revision_review AS
SELECT a.company,a.period_id,a.item_id,
 a.observation_id AS latest_api_observation_id,b.observation_id AS reviewed_observation_id,
 a.value_usd AS latest_api_value_usd,b.value_usd AS reviewed_value_usd,
 a.filed_date AS latest_api_filed,b.filed_date AS reviewed_filed,
 CASE WHEN a.candidate_count<>1 OR b.candidate_count<>1 THEN 'ambiguous_publication'
      WHEN b.observation_id IS NULL THEN 'no_reviewed_pdf_source'
      WHEN a.value_usd<>b.value_usd THEN 'unreviewed_value_change'
      WHEN a.observation_id<>b.observation_id THEN 'newer_api_same_amount_baseline_retained'
      ELSE 'same_publication' END AS review_status
FROM v_latest_api_candidate a LEFT JOIN v_reviewed_baseline_candidate b USING(company,period_id,item_id);

CREATE VIEW v_current_financial_items AS
SELECT o.observation_id,o.company,p.fiscal_year AS data_fiscal_year,o.item_id AS item,
 p.period_type,p.period_start,p.period_end,o.unit AS api_unit,
 o.value_usd AS canonical_amount_usd,e.source_signed_amount_usd,e.source_token,e.normalization,
 o.publication_fiscal_year,o.accession AS publication_accession,o.filed_date AS publication_filed,
 ps.path AS pdf_path,ps.sha256 AS pdf_sha256,e.pdf_page,e.source_label,
 aps.path AS api_path,aps.sha256 AS api_sha256,o.api_tag,o.value_usd AS api_value_usd,
 0 AS difference_usd,e.comparability_note,'matched' AS status,r.review_status,o.sec_url
FROM v_revision_review r JOIN v_eligible_observations o ON o.observation_id=r.reviewed_observation_id
JOIN dim_period p USING(period_id,company)
JOIN pdf_evidence e ON e.observation_id=o.observation_id
JOIN dim_source ps ON ps.source_id=e.pdf_source_id
JOIN dim_source aps ON aps.source_id=o.api_source_id
WHERE r.review_status IN ('same_publication','newer_api_same_amount_baseline_retained');

CREATE VIEW v_api_revision_history AS
SELECT o.company,p.fiscal_year,o.item_id,o.accession,f.form,f.filed_date,
 p.period_type,p.period_start,p.period_end,o.value_usd,o.api_tag,
 CASE WHEN e.observation_id IS NOT NULL THEN 1 ELSE 0 END AS pdf_reconciled,
 s.path AS api_path,o.observation_id
FROM fact_observation o JOIN dim_period p USING(period_id,company)
JOIN dim_filing f USING(accession,company) JOIN dim_source s ON s.source_id=o.api_source_id
LEFT JOIN pdf_evidence e ON e.observation_id=o.observation_id;

CREATE VIEW v_annual_inputs AS
SELECT p.company,p.fiscal_year,p.period_start,p.period_end,
 MAX(CASE WHEN item='net_revenue' THEN canonical_amount_usd END) AS net_revenue,
 MAX(CASE WHEN item='operating_expenses' THEN canonical_amount_usd END) AS operating_expenses,
 MAX(CASE WHEN item='operating_income' THEN canonical_amount_usd END) AS operating_income,
 MAX(CASE WHEN item='net_income' THEN canonical_amount_usd END) AS net_income,
 MAX(CASE WHEN item='operating_cash_flow' THEN canonical_amount_usd END) AS operating_cash_flow,
 MAX(CASE WHEN item='capital_payments_primary' THEN canonical_amount_usd END) AS capital_payments_primary,
 MAX(CASE WHEN item='capitalized_software_payments' THEN canonical_amount_usd END) AS capitalized_software_payments,
 MAX(CASE WHEN item='cash_and_cash_equivalents' THEN canonical_amount_usd END) AS cash_and_cash_equivalents,
 MAX(CASE WHEN item='current_debt' THEN canonical_amount_usd END) AS current_debt,
 MAX(CASE WHEN item='noncurrent_debt' THEN canonical_amount_usd END) AS noncurrent_debt,
 MAX(CASE WHEN item='total_assets' THEN canonical_amount_usd END) AS total_assets,
 MAX(CASE WHEN item='total_liabilities' THEN canonical_amount_usd END) AS total_liabilities
FROM dim_period p LEFT JOIN v_current_financial_items f
 ON f.company=p.company AND f.data_fiscal_year=p.fiscal_year
WHERE p.period_type='duration'
GROUP BY p.company,p.fiscal_year,p.period_start,p.period_end;

CREATE VIEW v_annual_kpis AS
WITH components AS (
 SELECT *,
 CASE WHEN company='visa' THEN capital_payments_primary
      WHEN company='mastercard' THEN capital_payments_primary+capitalized_software_payments END AS capital_payments_defined,
 current_debt+noncurrent_debt AS debt_carrying_total,
 CASE WHEN net_revenue>0 THEN 1.0*operating_income/net_revenue END AS operating_margin,
 CASE WHEN net_revenue>0 THEN 1.0*net_income/net_revenue END AS net_margin,
 CASE WHEN net_revenue>0 THEN 1.0*operating_expenses/net_revenue END AS operating_expense_ratio,
 CASE WHEN net_income>0 THEN 1.0*operating_cash_flow/net_income END AS cash_conversion
 FROM v_annual_inputs
), derived AS (
 SELECT *,operating_cash_flow-capital_payments_defined AS fcf_proxy,
 debt_carrying_total-cash_and_cash_equivalents AS net_debt_cash_equivalents_proxy,
 CASE WHEN total_assets>0 THEN 1.0*debt_carrying_total/total_assets END AS debt_to_assets,
 CASE WHEN total_assets>0 THEN 1.0*total_liabilities/total_assets END AS liabilities_to_assets,
 CASE WHEN debt_carrying_total>0 THEN 1.0*cash_and_cash_equivalents/debt_carrying_total END AS cash_to_debt_ratio
 FROM components
), prior AS (
 SELECT *,LAG(fiscal_year) OVER w AS prior_year,
 LAG(net_revenue) OVER w AS prior_revenue,LAG(operating_income) OVER w AS prior_operating_income,
 LAG(operating_margin) OVER w AS prior_operating_margin
 FROM derived WINDOW w AS (PARTITION BY company ORDER BY fiscal_year)
)
SELECT *,
 CASE WHEN net_revenue>0 THEN 1.0*fcf_proxy/net_revenue END AS fcf_proxy_margin,
 CASE WHEN prior_year=fiscal_year-1 AND prior_revenue>0 THEN 1.0*net_revenue/prior_revenue-1 END AS net_revenue_yoy_growth,
 CASE WHEN prior_year=fiscal_year-1 AND prior_operating_income>0 THEN 1.0*operating_income/prior_operating_income-1 END AS operating_income_yoy_growth,
 CASE WHEN prior_year=fiscal_year-1 THEN 100*(operating_margin-prior_operating_margin) END AS operating_margin_yoy_change
FROM prior;

CREATE VIEW v_kpi_values AS
WITH long_values AS (
 SELECT a.company,a.fiscal_year,a.period_start,a.period_end,k.metric_id,k.unit,k.status AS definition_status,k.period_rule,
 CASE k.metric_id
 WHEN 'net_revenue' THEN net_revenue WHEN 'operating_expenses' THEN operating_expenses
 WHEN 'operating_income' THEN operating_income WHEN 'net_income' THEN net_income
 WHEN 'operating_cash_flow' THEN operating_cash_flow WHEN 'cash_and_cash_equivalents' THEN cash_and_cash_equivalents
 WHEN 'current_debt' THEN current_debt WHEN 'noncurrent_debt' THEN noncurrent_debt
 WHEN 'total_assets' THEN total_assets WHEN 'total_liabilities' THEN total_liabilities
 WHEN 'net_revenue_yoy_growth' THEN net_revenue_yoy_growth WHEN 'operating_income_yoy_growth' THEN operating_income_yoy_growth
 WHEN 'operating_margin' THEN operating_margin WHEN 'net_margin' THEN net_margin
 WHEN 'operating_expense_ratio' THEN operating_expense_ratio WHEN 'cash_conversion' THEN cash_conversion
 WHEN 'operating_margin_yoy_change' THEN operating_margin_yoy_change
 WHEN 'capital_payments_defined' THEN capital_payments_defined WHEN 'fcf_proxy' THEN fcf_proxy
 WHEN 'fcf_proxy_margin' THEN fcf_proxy_margin WHEN 'debt_carrying_total' THEN debt_carrying_total
 WHEN 'net_debt_cash_equivalents_proxy' THEN net_debt_cash_equivalents_proxy
 WHEN 'debt_to_assets' THEN debt_to_assets WHEN 'liabilities_to_assets' THEN liabilities_to_assets
 WHEN 'cash_to_debt_ratio' THEN cash_to_debt_ratio END AS value
 FROM v_annual_kpis a CROSS JOIN dim_kpi k
)
SELECT company,fiscal_year,metric_id,unit,
 CASE WHEN period_rule='instant_same_company' OR definition_status<>'ready' THEN NULL ELSE period_start END AS period_start,
 period_end,value,
 CASE WHEN definition_status<>'ready' THEN 'deferred' WHEN value IS NULL THEN 'undefined' ELSE 'defined' END AS status,
 CASE WHEN definition_status<>'ready' THEN 'source_mapping_pending'
      WHEN value IS NULL AND fiscal_year=2021 AND period_rule='consecutive_annual_same_company' THEN 'missing_verified_prior_year'
      WHEN value IS NULL THEN 'missing_ineligible_input_or_nonpositive_denominator'
      ELSE NULL END AS reason
FROM long_values;

CREATE VIEW v_kpi_lineage AS
SELECT k.company,k.fiscal_year,k.metric_id,k.status,b.year_offset,
 f.data_fiscal_year AS source_fiscal_year,f.item,f.observation_id,f.canonical_amount_usd,
 f.publication_accession,f.publication_filed,f.pdf_path,f.pdf_page,f.api_path,f.api_tag
FROM v_kpi_values k JOIN bridge_kpi_input b USING(company,metric_id)
JOIN v_current_financial_items f ON f.company=k.company AND f.item=b.item_id
 AND f.data_fiscal_year=k.fiscal_year+b.year_offset;

-- BEGIN FILE: 03_example_queries.sql
-- example: summary
-- Amounts are USD millions. Each issuer keeps its actual fiscal dates.
SELECT company,fiscal_year,period_start,period_end,
       net_revenue/1000000.0 AS net_revenue_usd_million,
       ROUND(operating_margin*100,2) AS operating_margin_pct,
       ROUND(cash_conversion,2) AS ocf_to_net_income_x,
       fcf_proxy/1000000.0 AS defined_fcf_proxy_usd_million
FROM v_annual_kpis
WHERE (:company IS NULL OR company=:company)
  AND (:year IS NULL OR fiscal_year=:year)
ORDER BY company,fiscal_year;

-- example: lineage
-- One result per source leaf, not one financial result to sum.
SELECT company,fiscal_year,metric_id,item,source_fiscal_year,
       canonical_amount_usd,publication_accession,pdf_path,pdf_page,api_tag
FROM v_kpi_lineage
WHERE metric_id='operating_margin'
  AND (:company IS NULL OR company=:company)
  AND (:year IS NULL OR fiscal_year=:year)
ORDER BY company,fiscal_year,item;

-- example: revisions
-- Latest API publication and latest PDF-reviewed baseline can differ.
SELECT r.company,p.fiscal_year,r.item_id,r.latest_api_filed,r.reviewed_filed,
       r.latest_api_value_usd,r.reviewed_value_usd,r.review_status
FROM v_revision_review r JOIN dim_period p USING(period_id,company)
WHERE (:company IS NULL OR r.company=:company)
  AND (:year IS NULL OR p.fiscal_year=:year)
ORDER BY r.company,p.fiscal_year,r.item_id;

-- example: eligibility
SELECT company,fiscal_year,metric_id,value,unit,status,reason
FROM v_kpi_values
WHERE status<>'defined'
  AND (:company IS NULL OR company=:company)
  AND (:year IS NULL OR fiscal_year=:year)
ORDER BY company,fiscal_year,metric_id;


-- BEGIN FILE: 04_industry_metrics.sql
-- Operating disclosures have PDF evidence, not a fabricated XBRL match.
CREATE TABLE IF NOT EXISTS fact_industry_metric (
 company TEXT NOT NULL, fiscal_year INTEGER NOT NULL, metric_id TEXT NOT NULL,
 unit TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
 value INTEGER NOT NULL CHECK(value>0), basis TEXT NOT NULL, scope TEXT NOT NULL,
 evidence_json TEXT NOT NULL,
 PRIMARY KEY(company,fiscal_year,metric_id),
 FOREIGN KEY(company) REFERENCES dim_company(company),
 FOREIGN KEY(metric_id) REFERENCES dim_kpi(metric_id)
);
CREATE VIEW IF NOT EXISTS v_statement_kpi_values AS
WITH long_values AS (
 SELECT a.company,a.fiscal_year,a.period_start,a.period_end,k.metric_id,k.unit,k.status AS definition_status,k.period_rule,
 CASE k.metric_id
 WHEN 'net_revenue' THEN net_revenue WHEN 'operating_expenses' THEN operating_expenses
 WHEN 'operating_income' THEN operating_income WHEN 'net_income' THEN net_income
 WHEN 'operating_cash_flow' THEN operating_cash_flow WHEN 'cash_and_cash_equivalents' THEN cash_and_cash_equivalents
 WHEN 'current_debt' THEN current_debt WHEN 'noncurrent_debt' THEN noncurrent_debt
 WHEN 'total_assets' THEN total_assets WHEN 'total_liabilities' THEN total_liabilities
 WHEN 'net_revenue_yoy_growth' THEN net_revenue_yoy_growth WHEN 'operating_income_yoy_growth' THEN operating_income_yoy_growth
 WHEN 'operating_margin' THEN operating_margin WHEN 'net_margin' THEN net_margin
 WHEN 'operating_expense_ratio' THEN operating_expense_ratio WHEN 'cash_conversion' THEN cash_conversion
 WHEN 'operating_margin_yoy_change' THEN operating_margin_yoy_change
 WHEN 'capital_payments_defined' THEN capital_payments_defined WHEN 'fcf_proxy' THEN fcf_proxy
 WHEN 'fcf_proxy_margin' THEN fcf_proxy_margin WHEN 'debt_carrying_total' THEN debt_carrying_total
 WHEN 'net_debt_cash_equivalents_proxy' THEN net_debt_cash_equivalents_proxy
 WHEN 'debt_to_assets' THEN debt_to_assets WHEN 'liabilities_to_assets' THEN liabilities_to_assets
 WHEN 'cash_to_debt_ratio' THEN cash_to_debt_ratio END AS value
 FROM v_annual_kpis a CROSS JOIN dim_kpi k
)
SELECT company,fiscal_year,metric_id,unit,
 CASE WHEN period_rule='instant_same_company' OR definition_status<>'ready' THEN NULL ELSE period_start END AS period_start,
 period_end,value,
 CASE WHEN definition_status<>'ready' THEN 'deferred' WHEN value IS NULL THEN 'undefined' ELSE 'defined' END AS status,
 CASE WHEN definition_status<>'ready' THEN 'source_mapping_pending'
      WHEN value IS NULL AND fiscal_year=2021 AND period_rule='consecutive_annual_same_company' THEN 'missing_verified_prior_year'
      WHEN value IS NULL THEN 'missing_ineligible_input_or_nonpositive_denominator'
      ELSE NULL END AS reason
FROM long_values;


DROP VIEW IF EXISTS v_kpi_values;
CREATE VIEW v_kpi_values AS
SELECT * FROM v_statement_kpi_values
WHERE metric_id NOT IN ('payment_volume','network_transactions','customer_incentives')
UNION ALL
SELECT company,fiscal_year,metric_id,unit,period_start,period_end,value,
       'defined' AS status,NULL AS reason
FROM fact_industry_metric;
CREATE VIEW IF NOT EXISTS v_industry_lineage AS
SELECT company,fiscal_year,metric_id,value,unit,basis,scope,
       json_extract(evidence_json,'$.evidence.pdf_path') AS pdf_path,
       json_extract(evidence_json,'$.evidence.pdf_pages') AS pdf_pages,
       json_extract(evidence_json,'$.evidence.sha256') AS pdf_sha256,
       json_extract(evidence_json,'$.evidence.url') AS source_url
FROM fact_industry_metric;
