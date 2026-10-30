"""Verified historical operating disclosures, separate from SEC-XBRL reconciliation."""
import hashlib
import json
import sqlite3
from functools import lru_cache
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

@lru_cache(maxsize=1)
def verified_rows():
    rows=json.loads((ROOT/'references/industry_evidence.json').read_text())['rows']
    assert len(rows)==30
    seen=set()
    for row in rows:
        key=row['company'],row['fiscal_year'],row['metric_id']
        assert key not in seen
        seen.add(key)
        e=row['evidence'];path=ROOT/e['pdf_path']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==e['sha256'],path
        assert row['value']>0 and row['status']=='verified'
        assert row['period_end']==f"{row['fiscal_year']}-"+('09-30' if row['company']=='visa' else '12-31')
        if 'gross_components_usdm' in row:
            assert sum(row['gross_components_usdm'])==row['gross_usdm']
            assert row['gross_usdm']-row['value']//10**6==row['net_usdm']
            assert abs(row['bridge_residual_usdm'] or 0)<=1
    return rows

def install(path=None):
    """Extend a frozen model without changing any reviewed statement observation."""
    from financial_reconciliation import read_sql
    rows=verified_rows()
    con=sqlite3.connect(path or ROOT/'models/payment_financials.sqlite')
    before=con.execute('SELECT COUNT(*),SUM(value_usd) FROM fact_observation').fetchone()
    with con:
        con.executescript(read_sql('04_industry_metrics.sql'))
        for row in rows:
            con.execute('INSERT OR REPLACE INTO fact_industry_metric VALUES (?,?,?,?,?,?,?,?,?,?)',
                (row['company'],row['fiscal_year'],row['metric_id'],row['unit'],row['period_start'],row['period_end'],
                 row['value'],row['basis'],row['scope'],json.dumps(row,sort_keys=True)))
        for m in json.loads((ROOT/'docs/04_finance_kpi_dictionary.json').read_text())['metrics']:
            if m['calculation']['op']=='industry_evidence':
                con.execute('UPDATE dim_kpi SET status=?,period_rule=?,definition_json=? WHERE metric_id=?',
                    ('ready',m['period_rule'],json.dumps(m,sort_keys=True),m['id']))
        assert con.execute('SELECT COUNT(*),SUM(value_usd) FROM fact_observation').fetchone()==before
        assert dict(con.execute('SELECT status,COUNT(*) FROM v_kpi_values GROUP BY status'))=={'defined':274,'undefined':6}
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    con.close()

def verify_pdf_values():
    """Recheck PDF tokens and rebuild the public industry CSV from the evidence ledger."""
    import csv,re
    from pypdf import PdfReader
    documents={};flat=[]
    for row in verified_rows():
        e=row['evidence'];p=e['pdf_path']
        if p not in documents:documents[p]=PdfReader(ROOT/p)
        tokens=set(re.findall(r'(?<!\d)\d[\d,]*',' '.join(documents[p].pages[n-1].extract_text() for n in e['pdf_pages'])))
        for value in e['tokens']:assert f'{value:,}' in tokens or str(value) in tokens,(p,e['pdf_pages'],value)
        if 'gross_components_usdm' in row:
            assert row['value']//10**6==row['gross_usdm']-row['net_usdm']
        else:assert row['value']//row['source_scale'] in e['tokens']
        flat.append({k:v for k,v in row.items() if k not in ['evidence','gross_components_usdm']} |
            {'pdf_path':p,'pdf_pages':','.join(map(str,e['pdf_pages'])),'source_url':e['url'],'source_sha256':e['sha256']})
    fields=list(dict.fromkeys(k for r in flat for k in r))
    with (ROOT/'data/processed/industry_metrics.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(sorted(flat,key=lambda r:(r['company'],r['fiscal_year'],r['metric_id'])))
    return {'contexts':len(flat),'source_pdfs':len(documents),'all_pdf_tokens_present':True,'all_derivations_reconciled':True}

if __name__=='__main__':
    import sys
    if '--verify-pdfs' in sys.argv:print(json.dumps(verify_pdf_values()))
    else:
        install()
        print('30 verified issuer-specific metrics installed. Statement observations unchanged.')
