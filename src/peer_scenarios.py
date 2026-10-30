from reproducibility import snapshot,check_snapshot
"""Historical peer restrictions and an explicit margin-driven hypothetical model."""
import csv
import json
from fractions import Fraction
from financial_model import ROOT,load,sha,readonly
from financial_kpis import KPIEngine,read_contract,read_inputs,serialize

CASES={
 'visa':{'Base':{'growth':'0.10','margin':'0.60'},'Downside':{'growth':'0.04','margin':'0.56'},'Upside':{'growth':'0.14','margin':'0.62'}},
 'mastercard':{'Base':{'growth':'0.12','margin':'0.58'},'Downside':{'growth':'0.06','margin':'0.54'},'Upside':{'growth':'0.16','margin':'0.60'}}}

def rational(value):
    if value is None or isinstance(value,bool):raise ValueError('Missing or boolean input')
    try:return Fraction(str(value))
    except (ValueError,ZeroDivisionError):raise ValueError('Finite numeric input required') from None

def margin_scenario(base_revenue,base_expenses,growth,margin):
    revenue,expenses,g,m=map(rational,[base_revenue,base_expenses,growth,margin])
    if revenue<=0 or expenses<0:raise ValueError('Positive base revenue and nonnegative operating expenses required')
    if g < -1:raise ValueError('Growth below -100% would create negative revenue')
    if not -1<=m<=1:raise ValueError('This illustrative model permits margins from -100% to 100%')
    projected_revenue=revenue*(1+g);income=projected_revenue*m;projected_expenses=projected_revenue-income
    actual_income=revenue-expenses
    return {'revenue':projected_revenue,'operating_income':income,'operating_expenses':projected_expenses,
        'operating_margin':None if projected_revenue==0 else income/projected_revenue,
        'revenue_change':projected_revenue-revenue,'operating_income_change':income-actual_income,
        'operating_income_growth':None if actual_income<=0 else income/actual_income-1,
        'implied_opex_growth':None if expenses<=0 else projected_expenses/expenses-1}

def plain(value):
    if value is None:return None
    return value.numerator if value.denominator==1 else float(value)

def main():
    protected=snapshot()
    check_snapshot(protected)
    contract=read_contract();inputs=read_inputs();engine=KPIEngine(contract,inputs)
    con=readonly()
    try:annual=[dict(x) for x in con.execute('SELECT * FROM v_annual_kpis ORDER BY company,fiscal_year')]
    finally:con.close()
    restrictions=[]
    for m in contract['metrics']:
        category='reported_fiscal_year_with_caveats'
        if m['status']!='ready':category='not_comparable_pending_mapping'
        elif m['id'] in ['capital_payments_defined','fcf_proxy','fcf_proxy_margin']:category='issuer_specific_proxy_scope'
        restrictions.append({'metric_id':m['id'],'status':category,'definition':m['definition_en'],
            'period_rule':m['period_rule'],'unit':m['storage_unit'],'specific_limit':m['limitations'],
            'peer_policy':m['peer_policy'],'calendar_aligned':False,'ranking_permitted':False,
            'source_artifact':'docs/04_finance_kpi_dictionary.json'})
    scenarios=[];sensitivities=[]
    for company in ['visa','mastercard']:
        a=next(x for x in annual if x['company']==company and x['fiscal_year']==2025)
        for name,drivers in CASES[company].items():
            values=margin_scenario(a['net_revenue'],a['operating_expenses'],drivers['growth'],drivers['margin'])
            scenarios.append({'company':company,'case':name,'baseline_fiscal_year':2025,
                'baseline_period_start':a['period_start'],'baseline_period_end':a['period_end'],
                'hypothetical_period_end':a['period_end'].replace('2025','2026'),
                'assumed_growth':float(drivers['growth']),'assumed_margin':float(drivers['margin']),
                'values_usd_or_ratio':{k:plain(v) for k,v in values.items()},
                'exact_values':{k:None if v is None else {'numerator':v.numerator,'denominator':v.denominator} for k,v in values.items()},
                'label':'Illustrative next-own-fiscal-year scenario; not company guidance, budget or actuals.'})
        for mcase in ['Downside','Base','Upside']:
            for gcase in ['Downside','Base','Upside']:
                g=CASES[company][gcase]['growth'];m=CASES[company][mcase]['margin']
                result=margin_scenario(a['net_revenue'],a['operating_expenses'],g,m)
                sensitivities.append({'company':company,'growth_case':gcase,'margin_case':mcase,
                    'assumed_growth':float(g),'assumed_margin':float(m),'operating_income_usd':plain(result['operating_income'])})
    ready=[m for m in contract['metrics'] if m['status']=='ready']
    expected=[serialize(engine,m['id'],c,y) for c in ['visa','mastercard'] for y in range(2021,2026) for m in ready]
    manifest=load('references/02_source_manifest.json');urls={x['path']:x['url'] for x in manifest['files']}
    payload={'chapter':9,'as_of_date':'2026-10-29','mode':'margin_driven','cases':CASES,'metrics':ready,
        'inputs':[dict(row,source_url=urls[row['pdf_path']]) for row in inputs],
        'annual':annual,'expected_kpis':expected,'scenarios':scenarios,'sensitivity':sensitivities,
        'comparability':restrictions,'model_sha256':sha(ROOT/'models/payment_financials.sqlite'),
        'limits':['Different issuer fiscal periods; no fabricated calendar-year or TTM adjustment.',
            'Thirty issuer-specific industry contexts are verified in the evidence ledger and Excel Industry sheet. The six FY2021 changes lack a verified FY2020 base. No rank or constant-currency claim.',
            'Illustrative margins and growth are chosen assumptions, not empirically estimated forecasts.',
            'Costs are implied; no independent conflicting cost driver. No scenario NI or FCF.',
            'Hypothetical period is the next own fiscal year after FY2025, not observed FY2026.']}
    (ROOT/'reports/09_scenario_inputs.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    exports=[('data/processed/09_peer_comparability.csv',restrictions),('data/processed/09_sensitivity.csv',sensitivities),
        ('data/processed/09_scenarios.csv',[{k:v for k,v in s.items() if k not in ['exact_values','values_usd_or_ratio']}|s['values_usd_or_ratio'] for s in scenarios])]
    for path,rows in exports:
        with (ROOT/path).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(json.dumps({'comparability_records':len(restrictions),'ready_kpi_contexts':len(expected),'scenarios':len(scenarios),'sensitivity_combinations':len(sensitivities)},indent=2))

if __name__=='__main__':main()
