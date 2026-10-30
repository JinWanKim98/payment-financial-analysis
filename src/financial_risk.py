from reproducibility import snapshot,check_snapshot
"""Descriptive investigation rules; no severity scores or default model."""
import csv
import json
from collections import Counter
from datetime import date
from fractions import Fraction
from financial_model import ROOT,sha,load,readonly
from financial_kpis import KPIEngine,read_contract,read_inputs,unique_lineage,display

def number(value):
    if value is None:return None
    return value.numerator if value.denominator==1 else float(value)

def directional(a,b,comparison):
    if a is None or b is None:return 'undefined'
    if type(a) is bool or type(b) is bool:raise ValueError('Booleans are not financial amounts')
    if not all(isinstance(v,(int,Fraction)) for v in [a,b]):raise ValueError('Exact rational operands required')
    if comparison not in ['<','>']:raise ValueError('Only explicit directional comparisons allowed')
    return 'observed' if (a<b if comparison=='<' else a>b) else 'not_observed'

def growth_pair(current,prior):
    if current is None or prior is None or prior<=0:return None
    return Fraction(current,prior)-1

def elapsed_days(as_of,source_date):
    days=(date.fromisoformat(as_of)-date.fromisoformat(source_date)).days
    if days<0:raise ValueError('Source date is after the analysis cutoff')
    return days

def evaluate_signal(engine,spec,company,year):
    mid=spec['id'];points=[]
    def read(metric,y):
        result=engine.evaluate(metric,company,y);points.append(result);return result['value']
    mapping={'cash_decrease':('cash_and_cash_equivalents','<'),
        'debt_increase':('debt_carrying_total','>'),'current_debt_increase':('current_debt','>'),
        'net_debt_increase':('net_debt_cash_equivalents_proxy','>'),'margin_decrease':('operating_margin','<')}
    reason=None;unit='USD';difference_unit='USD'
    if mid=='ocf_growth_lags_income':
        ocf,prior_ocf=read('operating_cash_flow',year),read('operating_cash_flow',year-1)
        income,prior_income=read('net_income',year),read('net_income',year-1)
        a,b=growth_pair(ocf,prior_ocf),growth_pair(income,prior_income)
        comparison='<';unit=difference_unit='fraction'
        if a is None or b is None:reason='missing_input_or_nonpositive_prior_growth_base'
    else:
        if mid not in mapping:raise ValueError('Unknown change rule: '+mid)
        metric,comparison=mapping[mid];a,b=read(metric,year),read(metric,year-1)
        if mid=='margin_decrease':unit='fraction';difference_unit='percentage_points'
    if any(p['value'] is None for p in points):
        reason='missing_or_invalid_input'
        if year-1 not in engine.contract['target_fiscal_years']:reason='missing_verified_prior_year'
    status=directional(a,b,comparison)
    delta=None if a is None or b is None else (a-b)*(100 if mid=='margin_decrease' else 1)
    lineage=unique_lineage(points)
    return {'company':company,'fiscal_year':year,'signal_id':mid,'label_en':spec['label_en'],
        'status':status,'reason':reason if status=='undefined' else None,'comparison':comparison,
        'operand_a':number(a),'operand_b':number(b),'operand_unit':unit,'difference':number(delta),
        'difference_unit':difference_unit,'difference_exact':None if delta is None else {'numerator':delta.numerator,'denominator':delta.denominator},
        'source_rows':lineage,'meaning':'Directional pattern only; not observed does not mean safe, and observed is not a probability.'}

def build_results():
    protected=snapshot()
    check_snapshot(protected)
    plan=load('docs/08_risk_indicator_plan.json');contract=read_contract();inputs=read_inputs()
    engine=KPIEngine(contract,inputs);lookup={(x['company'],int(x['data_fiscal_year']),x['item']):x for x in inputs}
    con=readonly()
    try:annual=[dict(x) for x in con.execute('SELECT * FROM v_annual_kpis ORDER BY company,fiscal_year')]
    finally:con.close()
    history=[]
    for row in annual:
        c,y=row['company'],row['fiscal_year']
        fields=['company','fiscal_year','period_start','period_end','cash_and_cash_equivalents','current_debt','noncurrent_debt',
            'debt_carrying_total','net_debt_cash_equivalents_proxy','cash_to_debt_ratio','debt_to_assets','operating_margin','cash_conversion']
        history.append({k:row[k] for k in fields})
        for metric in ['debt_carrying_total','net_debt_cash_equivalents_proxy','cash_to_debt_ratio']:
            exact=engine.evaluate(metric,c,y)['value'];assert exact is not None
            assert abs(float(exact)-row[metric])<=1e-12
    signals=[evaluate_signal(engine,s,c,y) for c in contract['companies'] for y in plan['baseline_fiscal_years'] for s in plan['signals']]
    assert len(signals)==60 and sum(x['status']=='undefined' for x in signals)==12
    latest={};registry=[];evidence=load('references/08_disclosure_evidence.json')
    evidence_ids={e['id'] for e in evidence['pages']}
    for c in contract['companies']:
        def value(metric,year=2025):
            r=engine.evaluate(metric,c,year);assert r['value'] is not None;return r['value']
        keys=['cash_and_cash_equivalents','current_debt','noncurrent_debt','operating_income','net_revenue','net_income','operating_cash_flow']
        context_rows=[lookup[c,y,i] for y in [2024,2025] for i in keys]
        source=lookup[c,2025,'net_revenue'];period_end=source['period_end'];filed=source['publication_filed']
        cash=value('cash_and_cash_equivalents');debt=value('debt_carrying_total');net=value('net_debt_cash_equivalents_proxy')
        previous_cash=value('cash_and_cash_equivalents',2024);previous_debt=value('debt_carrying_total',2024)
        cf_total=lookup[c,2025,'cash_flow_total_end'];assert cf_total['status']=='matched' and cf_total['api_unit']=='USD'
        ratio=value('cash_to_debt_ratio')
        latest[c]={'company':c,'fiscal_year':2025,'period_start':source['period_start'],'period_end':period_end,
            'filed_date':filed,'analysis_as_of_date':plan['as_of_date'],
            'cash_equivalents_usd':number(cash),'selected_debt_usd':number(debt),'net_debt_proxy_usd':number(net),
            'cash_change_usd':number(cash-previous_cash),'debt_change_usd':number(debt-previous_debt),
            'net_debt_proxy_change_usd':number(net-value('net_debt_cash_equivalents_proxy',2024)),
            'current_debt_usd':number(value('current_debt')),'prior_current_debt_usd':number(value('current_debt',2024)),
            'current_debt_growth_fraction':number(growth_pair(value('current_debt'),value('current_debt',2024))),
            'current_debt_growth_reason':'nonpositive_prior_base' if value('current_debt',2024)<=0 else None,
            'cash_flow_ending_total_usd':int(cf_total['canonical_amount_usd']),
            'cash_to_selected_debt_display':display(ratio,'multiple'),
            'cash_to_selected_debt_exact':{'numerator':ratio.numerator,'denominator':ratio.denominator},
            'operating_margin_display':display(value('operating_margin'),'fraction'),
            'cash_conversion_display':display(value('cash_conversion'),'multiple'),
            'days_since_period_end':elapsed_days(plan['as_of_date'],period_end),
            'days_since_filed':elapsed_days(plan['as_of_date'],filed),
            'numeric_source_rows':context_rows+[cf_total]}
        s=latest[c]
        liquidity=[c+'_liquidity_obligations']
        settlement=['visa_settlement_guarantee'] if c=='visa' else ['mastercard_settlement_note_start','mastercard_settlement_basis']
        cyber=['visa_cyber_program'] if c=='visa' else ['mastercard_cyber_program','mastercard_cyber_residual_uncertainty']
        common_refs=['reports/07_analysis_results.json','references/07_issuer_explanations.json']
        def add(family,finding,question,cannot,gaps,disclosures,signal_ids=(),numeric=True):
            assert set(disclosures)<=evidence_ids
            registry.append({'id':c+'_'+family,'company':c,'fiscal_year':2025,'family':family,
                'finding_type':'data_limitation' if family=='source_freshness' else 'numeric_context_and_disclosure' if numeric else 'disclosure_only',
                'finding':finding,'followup_question':question,'cannot_conclude':cannot,'unresolved_inputs':gaps,
                'disclosure_ids':disclosures,'supporting_artifacts':common_refs if family in ['margin_legal_costs','cash_timing'] else ['reports/02_inventory_results.json','references/02_source_manifest.json'] if family=='source_freshness' else [],
                'relevant_signal_ids':list(signal_ids),'financial_source_rows':context_rows if numeric else [],
                'baseline_period_end':period_end,'analysis_as_of_date':plan['as_of_date'],
                'workflow':'Static investigation register; no ongoing monitoring or messages scheduled.'})
        add('liquidity_scope',f"Cash-equivalents {s['cash_equivalents_usd']} USD and cash/debt {s['cash_to_selected_debt_display']} describe selected balances, not available liquidity.",
            'What cash is available for general use after restricted/client funds and settlement buffers, and what other obligations require liquidity?',
            'No current ratio, freely distributable cash or maturity coverage conclusion.',
            ['Full available-cash reconciliation and maturity/obligation scope'],liquidity,['cash_decrease'])
        add('debt_refinancing',f"Selected carrying debt changed {s['debt_change_usd']} USD; debt-minus-cash changed {s['net_debt_proxy_change_usd']} USD. Both movements need context.",
            'How do principal maturities, interest/currency terms and subsequent repayments/new borrowings change the frozen carrying balances?',
            'Higher debt alone is not a default conclusion; carrying amounts are not principal maturities or all financing obligations.',
            ['Normalized principal maturity/interest schedule','Post-FY2025 repayments and new debt'],liquidity,
            ['debt_increase','current_debt_increase','net_debt_increase'])
        add('margin_legal_costs',f"Reported operating margin is {s['operating_margin_display']}; Chapter07 separates expense accruals, tax and cash.",
            'Which disclosed legal, regulatory and operating cost items recur, and how do later legal outcomes change expense and liquidity?',
            'Provision is not same-year cash paid or total potential legal exposure; no normalized profit or legal-outcome probability.',
            ['Independent recurrence assessment','Subsequent legal outcomes and payment timing'],liquidity,['margin_decrease'])
        add('cash_timing',f"Reported OCF/consolidated NI is {s['cash_conversion_display']}; own-year growth patterns and prior cash bridges are retained.",
            'How do incentives, tax timing and operating-balance changes explain cash support, without deducting supplemental cash twice?',
            'A ratio above1 is not proof of recurring cash or debt-service capacity.',
            ['Future timing/working-capital evidence; not a cash forecast'],liquidity,['ocf_growth_lags_income'])
        add('settlement_counterparty','The network has contingent settlement obligations and collateral/mitigation arrangements.',
            'What exposure basis, customer concentration, collateral availability and stressed settlement needs are disclosed on comparable dates?',
            'Disclosed exposure is not expected loss; Visa daily maxima and Mastercard estimated end-date exposure are not a common denominator.',
            ['Comparable exposure definition','Counterparty default/loss data and stress assumptions'],settlement,numeric=False)
        add('operational_cyber','The issuer describes cybersecurity/resilience and third-party dependencies.',
            'Do subsequent material incident, outage or third-party disclosures affect service capacity, costs or financial reporting?',
            'Control/program descriptions do not establish incident absence or quantified residual risk.',
            ['Incident-level financial-impact data','Independent control effectiveness evidence'],cyber,numeric=False)
        add('source_freshness',f"FY2025 period end is {s['days_since_period_end']} days before the analysis date; filing age is {s['days_since_filed']} days.",
            'What later quarterly/annual filings or material events must be reviewed before using these historical signals for a current decision?',
            'The historical baseline does not establish2026 conditions or whether a stated past maturity was repaid.',
            ['Numeric integration of later filings','Subsequent events reviewed only when explicitly sourced'],[],numeric=False)
    assert len(registry)==14 and all(x['followup_question'] and x['cannot_conclude'] and x['unresolved_inputs'] for x in registry)
    result={'chapter':8,'status':'computed_investigation_indicators','plan_sha256':sha(ROOT/'docs/08_risk_indicator_plan.json'),
        'model_sha256':sha(ROOT/'models/payment_financials.sqlite'),'analysis_as_of_date':plan['as_of_date'],
        'history':history,'signals':signals,'signal_status_counts':dict(Counter(x['status'] for x in signals)),
        'fy2025_context':latest,'investigation_register':registry,
        'no_risk_scores_or_probabilities':True,'no_arbitrary_risk_cutoffs':True,'limits':plan['limits'],
        'next_chapter':'09 Peer Comparability and Scenarios'}
    return result

def main():
    r=build_results();(ROOT/'reports/08_risk_results.json').write_text(json.dumps(r,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    exports=[('data/processed/08_risk_history.csv',r['history']),
        ('data/processed/08_change_signals.csv',[{k:v for k,v in s.items() if k not in ['source_rows','difference_exact']}|{'source_rows_json':json.dumps(s['source_rows'],ensure_ascii=False)} for s in r['signals']]),
        ('data/processed/08_investigation_register.csv',[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in row.items()} for row in r['investigation_register']])]
    for name,rows in exports:
        with (ROOT/name).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(json.dumps({'history_rows':len(r['history']),'directional_contexts':len(r['signals']),
        'status_counts':r['signal_status_counts'],'investigation_records':len(r['investigation_register']),
        'fy2025_balances':{c:{k:s[k] for k in ['cash_equivalents_usd','selected_debt_usd','net_debt_proxy_usd','net_debt_proxy_change_usd','days_since_period_end']} for c,s in r['fy2025_context'].items()}},indent=2))

if __name__=='__main__':main()
