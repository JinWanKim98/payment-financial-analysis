"""Build source/period inventories; do not select or reconcile KPI amounts."""
import csv
import json
import re
from datetime import date,datetime,timezone
from pathlib import Path
from prepare_sources import ROOT,sha,verify

REQUIRED=['accessionNumber','filingDate','reportDate','form','primaryDocument']

def filing_rows(document):
    recent=document['filings']['recent']
    assert all(k in recent for k in REQUIRED),'Missing submission columns'
    lengths={len(recent[k]) for k in REQUIRED}
    assert len(lengths)==1,'Misaligned submission arrays'
    return [{k:recent[k][i] for k in REQUIRED} for i in range(len(recent['form']))]

def filing_url(cik,row):
    assert re.fullmatch(r'\d{10}',str(cik))
    assert re.fullmatch(r'\d{10}-\d{2}-\d{6}',row['accessionNumber'])
    assert re.fullmatch(r'[A-Za-z0-9_.-]+',row['primaryDocument'])
    assert '..' not in row['primaryDocument']
    return 'https://www.sec.gov/Archives/edgar/data/'+str(int(cik))+'/'+row['accessionNumber'].replace('-','')+'/'+row['primaryDocument']

def select_filings(document,month_day,cutoff,years=range(2021,2026)):
    rows=[r for r in filing_rows(document) if r['filingDate']<=cutoff]
    annual=[]
    for year in years:
        candidates=[r for r in rows if r['form']=='10-K' and r['reportDate']==f'{year}-{month_day}']
        assert len(candidates)==1,('Annual filing not unique',year,len(candidates))
        annual.append(candidates[0])
    amendments=[r for r in rows if r['form']=='10-K/A' and r['reportDate'] in {a['reportDate'] for a in annual}]
    quarters=[r for r in rows if r['form']=='10-Q' and r['reportDate']>annual[-1]['reportDate']]
    latest=max(quarters,key=lambda r:(r['reportDate'],r['filingDate'],r['accessionNumber'])) if quarters else None
    return annual,latest,amendments

def duration_contexts(facts,accn,end,tag='NetCashProvidedByUsedInOperatingActivities'):
    records=facts.get('facts',{}).get('us-gaap',{}).get(tag,{}).get('units',{}).get('USD',[])
    fields=['start','end','accn','fy','fp','form','filed','frame']
    return [{k:r[k] for k in fields if k in r} for r in records
            if r.get('accn')==accn and r.get('end')==end and r.get('start')]

def annual_context(facts,row):
    contexts=duration_contexts(facts,row['accessionNumber'],row['reportDate'])
    contexts=[c for c in contexts if c['form']=='10-K' and c['fp']=='FY'
              and 364<=(date.fromisoformat(c['end'])-date.fromisoformat(c['start'])).days+1<=366]
    starts={c['start'] for c in contexts}
    assert len(starts)==1,('Missing/ambiguous annual duration',row['accessionNumber'],starts)
    assert all(c['filed']==row['filingDate'] for c in contexts),'API filing-date mismatch'
    return {'start':contexts[0]['start'],'end':contexts[0]['end'],'context_tag':'us-gaap:NetCashProvidedByUsedInOperatingActivities',
            'context_unit':'USD','source_accession':row['accessionNumber'],
            'basis':'Period metadata only; the amount was not selected or reconciled'}

def api_profile(document):
    namespaces={};all_obs=[]
    for tax,tags in document['facts'].items():
        obs=[r for t in tags.values() for arr in t['units'].values() for r in arr]
        all_obs+=obs
        namespaces[tax]={'tags':len(tags),'observations':len(obs),
                         'units':sorted({u for t in tags.values() for u in t['units']})}
    return {'taxonomies':namespaces,'total_observations':len(all_obs),
            'max_observed_end':max(r['end'] for r in all_obs),
            'max_observed_filed':max(r['filed'] for r in all_obs if 'filed' in r),
            'scope':'Record/namespace/unit metadata only; tag presence does not establish KPI coverage'}

def write_csv(path,rows):
    assert rows
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def build():
    manifest=verify();before={e['path']:e['sha256'] for e in manifest['files']}
    contract=json.loads((ROOT/'references/source_catalog.json').read_text())['framing']
    pdfs=json.loads((ROOT/'references/source_catalog.json').read_text())['pdf_review']['pdfs']
    catalog=[];coverage=[];amendments=[];quarters=[];profiles={}
    for company in contract['companies']:
        slug='visa' if company['ticker']=='V' else 'mastercard'
        submissions=json.loads((ROOT/f'data/raw/sec/{slug}/submissions.json').read_text())
        facts=json.loads((ROOT/f'data/raw/sec/{slug}/companyfacts.json').read_text())
        assert str(submissions['cik']).zfill(10)==company['cik']==str(facts['cik']).zfill(10)
        annual,quarter,amended=select_filings(submissions,company['fiscal_year_end_month_day'],manifest['as_of_date'])
        own=[p for p in pdfs if p['company']==slug]
        indexed={p['fiscal_year']:p for p in own}
        for row in annual+([quarter] if quarter else []):
            year=int(row['reportDate'][:4]);local=indexed.get(year) if row['form']=='10-K' else None
            catalog.append({'company':slug,'cik':company['cik'],'form':row['form'],
                'fiscal_year_label':year if row['form']=='10-K' else '',
                'report_period_end':row['reportDate'],'filed_date':row['filingDate'],
                'accession_number':row['accessionNumber'],'sec_primary_url':filing_url(company['cik'],row),
                'metadata_source':f'data/raw/sec/{slug}/submissions.json',
                'local_pdf_same_filing_year':local['path'] if local else '',
                'local_representation':'Issuer annual PDF; not SEC HTML bytes' if local else 'Metadata only',
                'scope':'Annual baseline inventory' if row['form']=='10-K' else 'Optional latest10-Q; not added to annual baseline'})
        assert annual[-1]['accessionNumber']==company['anchor_accession']
        for row in annual:
            year=int(row['reportDate'][:4]);ctx=annual_context(facts,row)
            flow=max((p for p in own if year in p['statement_year_columns']['operations'] and year in p['statement_year_columns']['cashflow']),key=lambda p:p['fiscal_year'])
            balance=max((p for p in own if year in p['statement_year_columns']['balance']),key=lambda p:p['fiscal_year'])
            coverage.append({'company':slug,'fiscal_year_label':year,'period_start':ctx['start'],'period_end':ctx['end'],
                'period_metadata_accession':ctx['source_accession'],'period_metadata_tag':ctx['context_tag'],
                'candidate_flow_pdf':flow['path'],'flow_publication_fy':flow['fiscal_year'],
                'operations_pdf_page':flow['page_locations_1_based']['operations'],
                'cashflow_pdf_page':flow['page_locations_1_based']['cashflow'],
                'candidate_balance_pdf':balance['path'],'balance_publication_fy':balance['fiscal_year'],
                'balance_pdf_page':balance['page_locations_1_based']['balance'],
                'numeric_reconciliation':'Pending Chapter03; comparative columns are not relabeled as original-year filings'})
        for r in amended:amendments.append({'company':slug,**r})
        profiles[slug]=api_profile(facts)
        if quarter:
            contexts=duration_contexts(facts,quarter['accessionNumber'],quarter['reportDate'])
            contexts=[c for c in contexts if c['form']=='10-Q']
            quarters.append({'company':slug,'report_period_end':quarter['reportDate'],
                'filed_date':quarter['filingDate'],'accession_number':quarter['accessionNumber'],
                'sec_url':filing_url(company['cik'],quarter),'ocf_contexts_present_in_frozen_api':contexts,
                'available_ytd_start_dates':sorted({c['start'] for c in contexts}),
                'local_raw_10q_copy_acquired':False,'numeric_values_added_to_baseline':False,
                'status':'Metadata and browser cover reviewed; amount selection/quarter-YTD reconciliation deferred',
                'api_warning':None if contexts else 'Latest submission exists, but no matching OCF context in the frozen Company Facts snapshot; do not invent a value or infer API completeness.'})
    write_csv(ROOT/'docs/02_filing_inventory.csv',catalog)
    write_csv(ROOT/'docs/02_period_coverage.csv',coverage)
    manifest_rows=[{k:e.get(k,'') for k in ['company','kind','path','url','retrieved_at_utc','bytes','sha256']} for e in manifest['files']]
    write_csv(ROOT/'data_manifest.csv',manifest_rows)
    result={'chapter':2,'as_of_date':manifest['as_of_date'],'computed_at_utc':datetime.now(timezone.utc).isoformat(),
            'raw_files':len(manifest['files']),'raw_bytes':sum(e['bytes'] for e in manifest['files']),
            'sec_api_files':4,'issuer_annual_pdfs':6,'annual_filing_metadata_rows':10,'latest_quarter_metadata_rows':len(quarters),
            'filing_catalog':catalog,'five_year_period_coverage':coverage,'api_profiles':profiles,
            'latest_quarters':quarters,'annual_amendments_found_in_snapshot':amendments,
            'annual_filing_and_statement_header_coverage_verified':True,
            'five_year_metric_values_reconciled':False,'financial_performance_calculated':False,
            'raw_sec_html_acquired':False,'latest_10q_raw_copies_acquired':False,
            'representation_note':'SEC metadata/API bytes plus issuer-published annual PDF copies. No claim that PDFs equal SEC HTML bytes;2022/2024 original10-K files are inventoried by metadata, and their statement columns are covered by2023/2025 PDFs.',
            'revision_policy_for_next_chapter':'Candidates favor later comparative columns within acquired2025/2023/2021 documents. Chapter03 must compare original and later API accessions, investigate restatements and add sources if needed before freezing amounts.',
            'source_hashes':before,'source_files_unchanged':all(sha(ROOT/p)==v for p,v in before.items())}
    assert result['source_files_unchanged'] and len(coverage)==10 and len(catalog)==12
    (ROOT/'reports/02_inventory_results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['raw_files','raw_bytes','sec_api_files','issuer_annual_pdfs','annual_filing_metadata_rows','latest_quarter_metadata_rows','source_files_unchanged','five_year_metric_values_reconciled']},indent=2))
    return result

if __name__=='__main__':build()
