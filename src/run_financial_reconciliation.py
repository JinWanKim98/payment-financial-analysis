from reproducibility import snapshot,check_snapshot
"""Reconcile independent PDF rows to exact frozen API contexts and revisions."""
import csv
import json
from datetime import datetime,timezone
from prepare_sources import ROOT,sha,verify
from financial_reconciliation import resolve_api,api_candidates,applicable,item_tags,csv_cell

def load(path):return json.loads((ROOT/path).read_text())

def main():
    manifest=verify()
    protected=snapshot()
    check_snapshot(protected)
    plan=load('docs/03_reconciliation_plan.json');extraction=load('reports/03_pdf_statement_values.json')
    assert extraction['plan_sha256']==sha(ROOT/'docs/03_reconciliation_plan.json')
    inventory=load('reports/02_inventory_results.json')
    annual={};facts={};source_info={e['path']:e for e in manifest['files']}
    for company in ['visa','mastercard']:
        annual[company]={int(r['fiscal_year_label']):r for r in inventory['filing_catalog'] if r['company']==company and r['form']=='10-K'}
        facts[company]=load(f'data/raw/sec/{company}/companyfacts.json')
    items={i['id']:i for i in plan['items']}
    periods={(p['company'],p['fiscal_year_label']):p for p in inventory['five_year_period_coverage']}
    all_comparisons=[];problems=[]
    for row in extraction['rows']:
        company,year,item=row['company'],row['data_fiscal_year'],items[row['item']]
        publication=annual[company][row['publication_fiscal_year']]
        period=periods[company,year]
        start=period['period_start'] if item['period_type']=='duration' else None
        try:
            api=resolve_api(facts[company],item_tags(item,company,row['publication_fiscal_year']),publication['accession_number'],start,
                            period['period_end'],item['period_type'],publication['filed_date'])
            delta=api['value_usd']-row['canonical_amount_usd']
            status='matched' if delta==0 else 'amount_mismatch'
            api_path=f'data/raw/sec/{company}/companyfacts.json'
            result={**row,'period_start':start,'period_end':period['period_end'],'api_unit':'USD',
                'publication_accession':publication['accession_number'],'publication_filed':publication['filed_date'],
                'sec_primary_url':publication['sec_primary_url'],'pdf_download_url':source_info[row['pdf_path']]['url'],
                'api_path':api_path,'api_url':source_info[api_path]['url'],'api_sha256':source_info[api_path]['sha256'],
                'api_value_usd':api['value_usd'],'api_tag':api['records'][0]['tag'],
                'api_json_pointers':[x['json_pointer'] for x in api['records']],
                'api_reporting_fy_values':sorted({x.get('fy') for x in api['records']}),
                'comparability_note':('Issuer-specific capital-payment component; do not compare Visa combined technology line with Mastercard PPE alone' if item['id'] in ['capital_payments_primary','capitalized_software_payments']
                    else item.get('definition_caveat','Reported statement item; exact fiscal dates retained; no calendar normalization')),
                'duplicate_consistent_api_records':api['duplicate_consistent_records'],'difference_usd':delta,'status':status}
        except (AssertionError,KeyError) as exc:
            result={**row,'status':'api_context_problem','problem':str(exc)}
        all_comparisons.append(result)
        if result['status']!='matched':problems.append(result)
    # Select one source copy per item/year after recording all original and
    # comparative PDF observations. Ties are ambiguity, never arbitrary order.
    selected=[];revisions=[]
    for company in ['visa','mastercard']:
        for year in plan['target_fiscal_years']:
            for item in plan['items']:
                if not applicable(item,company,year):continue
                pool=[x for x in all_comparisons if x['company']==company and x['data_fiscal_year']==year and x['item']==item['id']]
                assert pool,(company,year,item['id'],'No PDF source')
                latest=max(x['publication_fiscal_year'] for x in pool)
                candidates=[x for x in pool if x['publication_fiscal_year']==latest]
                assert len(candidates)==1,(company,year,item['id'],'Ambiguous PDF source')
                chosen=candidates[0];selected.append(chosen)
                if chosen['status']!='matched':continue
                # Scan every original/later annual accession through FY2025;
                # do not use the current API's newest arbitrary record.
                for report_year,report in annual[company].items():
                    records=api_candidates(facts[company],item_tags(item,company,report_year),report['accession_number'],
                        chosen['period_start'],chosen['period_end'],item['period_type'])
                    if not records:continue
                    check=resolve_api(facts[company],item_tags(item,company,report_year),report['accession_number'],
                        chosen['period_start'],chosen['period_end'],item['period_type'],report['filed_date'])
                    revisions.append({'company':company,'data_fiscal_year':year,'item':item['id'],
                        'comparison_publication_fy':report_year,'comparison_accession':report['accession_number'],
                        'comparison_filed':report['filed_date'],'value_usd':check['value_usd'],
                        'selected_accession':chosen['publication_accession'],
                        'same_accession':report['accession_number']==chosen['publication_accession'],
                        'is_original_annual_accession':report_year==year,
                        'selected_value_usd':chosen['canonical_amount_usd'],
                        'difference_usd':check['value_usd']-chosen['canonical_amount_usd'],
                        'source_json_pointers':[x['json_pointer'] for x in check['records']]})
    changes=[x for x in revisions if x['difference_usd']!=0]
    checks=[]
    for company in ['visa','mastercard']:
        for year in plan['target_fiscal_years']:
            values={x['item']:x['canonical_amount_usd'] for x in selected if x['company']==company and x['data_fiscal_year']==year and x['status']=='matched'}
            identity_inputs=['net_revenue','operating_expenses','operating_income','pretax_income',
                'income_tax','net_income','total_assets','total_liabilities','total_equity']
            if not all(key in values for key in identity_inputs):continue
            for name,residual in [
                ('net_revenue_minus_operating_expenses_equals_operating_income',values['net_revenue']-values['operating_expenses']-values['operating_income']),
                ('pretax_less_tax_equals_consolidated_net_income',values['pretax_income']-values['income_tax']-values['net_income']),
                ('assets_equal_reported_liabilities_equity_and_separate_temporary_equity',values['total_assets']-values['total_liabilities']-values['total_equity']-values.get('redeemable_noncontrolling_interests',0))]:
                checks.append({'company':company,'data_fiscal_year':year,'check':name,'residual_usd':residual,
                    'temporary_equity_term':'Source-reported component included' if 'redeemable_noncontrolling_interests' in values else 'No separately presented component; no numeric zero staged'})
    identity_failures=[x for x in checks if x['residual_usd']!=0]
    result={'chapter':3,'computed_at_utc':datetime.now(timezone.utc).isoformat(),
        'status':'passed' if not problems and not changes and not identity_failures else 'needs_review',
        'plan_sha256':sha(ROOT/'docs/03_reconciliation_plan.json'),'pdf_observations_compared':len(all_comparisons),
        'selected_company_year_items':len(selected),'matched_pdf_observations':sum(x['status']=='matched' for x in all_comparisons),
        'unexplained_api_pdf_problems':len(problems),'api_revision_comparisons':len(revisions),
        'cross_accession_comparisons':sum(not x['same_accession'] for x in revisions),
        'original_annual_contexts_checked':sum(x['is_original_annual_accession'] for x in revisions),
        'revision_value_differences':len(changes),'accounting_identity_checks':len(checks),
        'accounting_identity_failures':len(identity_failures),'all_comparisons':all_comparisons,
        'selected':selected,'problems':problems,'revision_checks':revisions,'revision_differences':changes,
        'accounting_checks':checks,'absolute_tolerance_usd':0,
        'scope':'Annual raw financial items2021–2025 only. No margins/growth/FCF, debt score, scenario or quarterly performance calculated.',
        'csv_label_encoding':'NUL glyphs escaped as literal backslash-u0000 in CSV labels; raw text retained in JSON. Numeric values unchanged.',
        'excluded_or_conditional_items':[
            'Visa software is not separately staged; its source primary capital-payment row includes technology.',
            'Mastercard redeemable NCI is staged only when separately presented2021–2023; later absence is not a fabricated numeric zero.',
            'Industry values and revenue-deduction bridges are verified separately in references/industry_evidence.json; this table reconciles statement items.',
            'Optional10-Q coverage remains outside the annual amount gate; the Chapter02 Visa API gap remains recorded.'
        ],'protected_sources_and_prior_evidence_unchanged':all(sha(ROOT/p)==h for p,h in protected.items())}
    (ROOT/'reports/03_reconciliation_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    fields=['company','data_fiscal_year','item','period_type','period_start','period_end','api_unit',
        'canonical_amount_usd','source_signed_amount_usd','source_token','normalization','publication_fiscal_year',
        'publication_accession','publication_filed','pdf_path','pdf_sha256','pdf_page','source_label',
        'api_path','api_sha256','api_tag','api_value_usd','difference_usd','comparability_note','status']
    with (ROOT/'reports/03_reconciliation_ledger.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        writer.writerows({k:csv_cell(r.get(k,'')) for k in fields} for r in all_comparisons)
    if result['status']=='passed':
        target=ROOT/'data/processed/03_financial_items.csv';target.parent.mkdir(exist_ok=True)
        with target.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
            writer.writerows({k:csv_cell(r.get(k,'')) for k in fields} for r in selected)
    print(json.dumps({k:v for k,v in result.items() if k not in ['all_comparisons','selected','problems','revision_checks','revision_differences','accounting_checks']},ensure_ascii=False,indent=2))
    if problems:print(json.dumps([{'company':x['company'],'year':x['data_fiscal_year'],'item':x['item'],'publication':x['publication_fiscal_year'],'problem':x.get('problem'),'delta':x.get('difference_usd')} for x in problems],ensure_ascii=False,indent=2))
    assert result['status']=='passed','Read the saved QA problems; no processed staging was published.'

if __name__=='__main__':main()
