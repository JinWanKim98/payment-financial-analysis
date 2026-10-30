"""Build and audit the frozen annual SQLite reporting model, without network."""

from financial_reconciliation import read_sql
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from financial_kpis import ROOT,KPIEngine,read_contract,read_inputs,periods
from financial_reconciliation import csv_cell

DB_PATH=ROOT/'models/payment_financials.sqlite'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):return json.loads((ROOT/path).read_text())
def oid(company,year,item,accession):return f'{company}|{year}|{item}|{accession}'
def pid(company,year,kind):return f'{company}|{year}|{kind}'

def insert(con,table,**data):
    con.execute(f"INSERT INTO {table} ({','.join(data)}) VALUES ({','.join('?' for _ in data)})",tuple(data.values()))

def pointer(document,path):
    for part in path.lstrip('/').split('/'):
        document=document[int(part)] if isinstance(document,list) else document[part.replace('~1','/').replace('~0','~')]
    return document

def verify_prior():
    from reproducibility import snapshot,check_snapshot
    protected=snapshot(model=False)
    check_snapshot(protected)
    return protected

def leaf_terms(calc,metrics,company,offset=0):
    op=calc['op']
    if op in ['deferred','industry_evidence']:return set()
    if op=='growth':return {(calc['item'],offset),(calc['item'],offset-1)}
    if op=='pp_change':
        return leaf_terms(metrics[calc['metric']]['calculation'],metrics,company,offset)|leaf_terms(metrics[calc['metric']]['calculation'],metrics,company,offset-1)
    refs=[calc['arg']] if op=='reported' else calc['terms_by_company'][company] if op=='sum_by_company' else calc['args']
    found=set()
    for ref in refs:
        found|={(ref['item'],offset)} if 'item' in ref else leaf_terms(metrics[ref['metric']]['calculation'],metrics,company,offset)
    return found

def populate(con):
    """Populate an empty connection. All source values are from frozen evidence."""
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript(read_sql('01_schema.sql'))
    d=read_contract();plan=load('docs/03_reconciliation_plan.json')
    inv=load('reports/02_inventory_results.json');qa=load('reports/03_reconciliation_results.json')
    manifest=load('references/02_source_manifest.json');files={r['path']:r for r in manifest['files']}
    annual={r['accession_number']:r for r in inv['filing_catalog'] if r['form']=='10-K'}
    items={x['id']:x for x in plan['items']}
    with con:
        for company,c in d['companies'].items():
            cik=next(r['cik'] for r in inv['filing_catalog'] if r['company']==company)
            insert(con,'dim_company',company=company,name=c['label'],cik=cik,fiscal_year_end=c['fiscal_year_end'])
            for year in d['target_fiscal_years']:
                start,end=periods(d,company,year);date.fromisoformat(start);date.fromisoformat(end)
                for kind in ['duration','instant']:
                    insert(con,'dim_period',period_id=pid(company,year,kind),company=company,fiscal_year=year,
                        period_type=kind,period_start=start if kind=='duration' else None,period_end=end)
        for x in plan['items']:
            insert(con,'dim_item',item_id=x['id'],statement=x['statement'],period_type=x['period_type'],definition_json=json.dumps(x,ensure_ascii=False,sort_keys=True))
        for f in sorted(inv['filing_catalog'],key=lambda x:x['accession_number']):
            date.fromisoformat(f['filed_date']);date.fromisoformat(f['report_period_end'])
            insert(con,'dim_filing',accession=f['accession_number'],company=f['company'],form=f['form'],
                publication_fiscal_year=int(f['fiscal_year_label']) if f['fiscal_year_label']!='' else None,report_period_end=f['report_period_end'],
                filed_date=f['filed_date'],sec_url=f['sec_primary_url'])
        for s in sorted(manifest['files'],key=lambda x:x['key']):
            insert(con,'dim_source',source_id=s['key'],company=s['company'],kind=s['kind'],path=s['path'],url=s['url'],sha256=s['sha256'],retrieved_at_utc=s['retrieved_at_utc'])
        for c in d['companies']:
            a=next(f for f in annual.values() if f['company']==c and int(f['fiscal_year_label'])==2025)
            insert(con,'model_policy',company=c,anchor_accession=a['accession_number'],anchor_fiscal_year=2025,
                anchor_period_end=a['report_period_end'],filed_cutoff=a['filed_date'],source_snapshot=manifest['snapshot'])
        facts={c:load(f'data/raw/sec/{c}/companyfacts.json') for c in d['companies']}
        for r in sorted(qa['revision_checks'],key=lambda x:(x['company'],x['data_fiscal_year'],x['item'],x['comparison_accession'])):
            c,y,item,accession=r['company'],r['data_fiscal_year'],r['item'],r['comparison_accession']
            kind=items[item]['period_type'];start,end=periods(d,c,y)
            original=[pointer(facts[c],p) for p in r['source_json_pointers']]
            assert original and all(x['val']==r['value_usd'] and x['accn']==accession and x['form']=='10-K' and x['fp']=='FY' and x['end']==end and x.get('start')==(start if kind=='duration' else None) and x['filed']==r['comparison_filed'] for x in original)
            tag=r['source_json_pointers'][0].split('/')[3]
            obs=oid(c,y,item,accession)
            insert(con,'fact_observation',observation_id=obs,company=c,period_id=pid(c,y,kind),period_type=kind,
                item_id=item,accession=accession,unit='USD',value_usd=r['value_usd'],taxonomy='us-gaap',api_tag=tag,api_source_id=c+'_companyfacts')
            for p in sorted(r['source_json_pointers']):insert(con,'observation_api_pointer',observation_id=obs,json_pointer=p)
        for r in sorted(qa['all_comparisons'],key=lambda x:(x['company'],x['data_fiscal_year'],x['item'],x['publication_accession'])):
            assert r['status']=='matched' and r['difference_usd']==0
            insert(con,'pdf_evidence',observation_id=oid(r['company'],r['data_fiscal_year'],r['item'],r['publication_accession']),
                company=r['company'],canonical_amount_usd=r['canonical_amount_usd'],pdf_source_id=files[r['pdf_path']]['key'],pdf_page=r['pdf_page'],
                source_token=r['source_token'],source_signed_amount_usd=r['source_signed_amount_usd'],normalization=r['normalization'],
                source_label=csv_cell(r['source_label']),raw_source_label=r['source_label'],comparability_note=r['comparability_note'])
        metrics={m['id']:m for m in d['metrics']}
        for m in d['metrics']:
            insert(con,'dim_kpi',metric_id=m['id'],label_en=m['label_en'],unit=m['storage_unit'],status=m['status'],period_rule=m['period_rule'],definition_json=json.dumps(m,ensure_ascii=False,sort_keys=True))
            for c in d['companies']:
                for item,offset in sorted(leaf_terms(m['calculation'],metrics,c)):
                    insert(con,'bridge_kpi_input',metric_id=m['id'],company=c,item_id=item,year_offset=offset)
        inputs=['references/02_source_manifest.json','reports/02_inventory_results.json','docs/03_reconciliation_plan.json',
            'reports/03_reconciliation_results.json','data/processed/03_financial_items.csv','docs/04_finance_kpi_dictionary.json',
            'analysis.sql','src/financial_model.py']+[s['path'] for s in manifest['files']]
        for p in sorted(set(inputs)):insert(con,'model_input',path=p,sha256=sha(ROOT/p))
    con.executescript(read_sql('02_reporting_views.sql'))
    con.executescript(read_sql('04_industry_metrics.sql'))
    from industry_metrics import verified_rows
    with con:
        for row in verified_rows():
            con.execute('INSERT INTO fact_industry_metric VALUES (?,?,?,?,?,?,?,?,?,?)',
                (row['company'],row['fiscal_year'],row['metric_id'],row['unit'],row['period_start'],row['period_end'],row['value'],row['basis'],row['scope'],json.dumps(row,sort_keys=True)))


def readonly(path=DB_PATH):
    con=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    con.row_factory=sqlite3.Row;con.execute('PRAGMA foreign_keys=ON');con.execute('PRAGMA query_only=ON')
    return con

def audit(con):
    assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not con.execute('PRAGMA foreign_key_check').fetchall()
    rows=read_inputs();selected=[dict(x) for x in con.execute('SELECT * FROM v_current_financial_items')]
    assert len(selected)==158
    by_key={(r['company'],int(r['data_fiscal_year']),r['item']):r for r in selected}
    for original in rows:
        actual=by_key[original['company'],int(original['data_fiscal_year']),original['item']]
        for k,v in original.items():assert str(actual[k] if actual[k] is not None else '')==v,(k,original,actual)
    statuses=Counter(x[0] for x in con.execute('SELECT review_status FROM v_revision_review'))
    assert not (set(statuses)-{'same_publication','newer_api_same_amount_baseline_retained'}),statuses
    d=read_contract();engine=KPIEngine(d,rows);values=con.execute('SELECT * FROM v_kpi_values ORDER BY company,fiscal_year,metric_id').fetchall()
    assert len(values)==280
    comparisons=0;max_error=0.;lineage_refs=0
    for v in values:
        expected=engine.evaluate(v['metric_id'],v['company'],v['fiscal_year'])
        assert v['status']==expected['status'],dict(v)
        if expected['value'] is None:assert v['value'] is None
        else:
            unit=engine.metrics[v['metric_id']]['storage_unit']
            if unit=='USD':assert type(v['value']) is int and v['value']==expected['value']
            else:
                error=abs(v['value']-float(expected['value']));max_error=max(max_error,error)
                assert math.isfinite(v['value']) and error<=1e-12,(dict(v),expected)
            comparisons+=1
        if engine.metrics[v['metric_id']]['calculation']['op']=='industry_evidence':
            actual_leaves=con.execute('SELECT company,fiscal_year,metric_id FROM v_industry_lineage WHERE company=? AND fiscal_year=? AND metric_id=?',
                (v['company'],v['fiscal_year'],v['metric_id'])).fetchall()
        else:
            actual_leaves=con.execute('SELECT company,source_fiscal_year,item FROM v_kpi_lineage WHERE company=? AND fiscal_year=? AND metric_id=?',
                (v['company'],v['fiscal_year'],v['metric_id'])).fetchall()
        expected_leaves={(x['company'],int(x['data_fiscal_year']),x['item']) for x in expected['lineage']}
        assert {tuple(x) for x in actual_leaves}==expected_leaves,(dict(v),actual_leaves,expected_leaves)
        assert len(actual_leaves)==len(expected_leaves)
        lineage_refs+=len(actual_leaves)
    counts={x[0]:con.execute('SELECT COUNT(*) FROM '+x[0]).fetchone()[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()}
    return {'status':'passed_sql_model','table_counts':counts,'selected_source_items':len(selected),
        'revision_selection_statuses':dict(statuses),'sql_kpi_contexts':len(values),'defined_kpi_comparisons':comparisons,
        'kpi_statuses':dict(Counter(x['status'] for x in values)),
        'source_lineage_references_checked':lineage_refs,'ratio_absolute_tolerance':1e-12,'largest_ratio_difference':max_error,
        'source_integer_amount_tolerance_usd':0,'foreign_key_errors':0,'integrity_check':'ok',
        'quarterly_facts_selected':con.execute("SELECT COUNT(*) FROM v_current_financial_items c JOIN dim_filing f ON c.publication_accession=f.accession WHERE f.form NOT IN ('10-K','10-K/A')").fetchone()[0]}

def build(path=DB_PATH):
    verify_prior();path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    handle,tmp=tempfile.mkstemp(prefix='05-pending-',suffix='.sqlite',dir=path.parent);os.close(handle)
    try:
        con=sqlite3.connect(tmp)
        try:populate(con);result=audit(con)
        finally:con.close()
        os.replace(tmp,path)
        return result
    finally:
        if Path(tmp).exists():Path(tmp).unlink()

def main():
    with readonly() as con:result=audit(con)
    result.update(database=str(DB_PATH.relative_to(ROOT)),database_sha256=sha(DB_PATH),
        sqlite_version=sqlite3.sqlite_version,scope='Audit of the included model. Independent raw-source reconstruction is available through reproduce.py; see REPRODUCING.md.')
    (ROOT/'reports/model_validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
