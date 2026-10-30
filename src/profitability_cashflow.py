from reproducibility import snapshot,check_snapshot
"""Complete reported profit/cash bridges; source explanation is a separate layer."""
import csv
import json
from fractions import Fraction
from financial_model import ROOT,sha,load,readonly
from cashflow_bridge import close_bridge,complete_group_delta,cost_change_share
from financial_kpis import display

def main():
    protected=snapshot()
    check_snapshot(protected)
    plan=load('docs/07_bridge_plan.json');extracted=load('reports/07_pdf_components.json')
    assert extracted['plan_sha256']==sha(ROOT/'docs/07_bridge_plan.json')
    rows=extracted['rows'];assert len(rows)==156
    by_key={(r['company'],r['fiscal_year'],r['item']):r for r in rows};assert len(by_key)==156
    con=readonly()
    try:
        source=[dict(x) for x in con.execute('SELECT * FROM v_current_financial_items ORDER BY company,data_fiscal_year,item')]
        annual=[dict(x) for x in con.execute('SELECT * FROM v_annual_kpis ORDER BY company,fiscal_year')]
    finally:con.close()
    core={(r['company'],r['data_fiscal_year'],r['item']):r for r in source}
    def coreval(c,y,i):return core[c,y,i]['canonical_amount_usd']
    def val(c,y,i):return by_key[c,y,i]['amount_usd']
    checks=[];cash_bridges=[];cost_changes=[];tax_context=[]
    def check(name,c,y,actual,expected):
        assert actual==expected,(name,c,y,actual,expected)
        checks.append({'check':name,'company':c,'fiscal_year':y,'actual_usd':actual,'expected_usd':expected,'residual_usd':actual-expected})
    for c,config in plan['companies'].items():
        costs=[r['item'] for r in config['rows'] if r['role']=='operating_cost']
        adj=[r['item'] for r in config['rows'] if r['role']=='cash_adjustment']
        balances=[r['item'] for r in config['rows'] if r['role']=='operating_balance_change']
        capital=[r['item'] for r in config['rows'] if r['role']=='capital_payment']
        for y in [2023,2024,2025]:
            check('operating_cost_component_sum',c,y,sum(val(c,y,i) for i in costs),coreval(c,y,'operating_expenses'))
            bridge=close_bridge(val(c,y,'net_income'),[val(c,y,i) for i in adj],[val(c,y,i) for i in balances],val(c,y,'operating_cash_flow'))
            check('complete_ocf_statement_sum',c,y,bridge['operating_cash_flow'],val(c,y,'operating_cash_flow'))
            check('pdf_net_income_to_reviewed_core',c,y,val(c,y,'net_income'),coreval(c,y,'net_income'))
            check('pdf_ocf_to_reviewed_core',c,y,val(c,y,'operating_cash_flow'),coreval(c,y,'operating_cash_flow'))
            for item in capital:check('pdf_capital_component_to_reviewed_core',c,y,val(c,y,item),coreval(c,y,item))
            cash_bridges.append({'company':c,'fiscal_year':y,**bridge,
                'source_component_keys':[[c,y,i] for i in ['net_income',*adj,*balances,'operating_cash_flow']]})
            tax_context.append({'company':c,'fiscal_year':y,'tax_expense_usd':coreval(c,y,'income_tax'),
                'cash_taxes_paid_usd':val(c,y,'cash_taxes_paid'),'classification':'Supplemental context; not an extra OCF operand.',
                'cash_source_key':[c,y,'cash_taxes_paid'],'tax_expense_source':core[c,y,'income_tax']})
        for item in costs:
            cost_changes.append({'company':c,'item':item,'label':by_key[c,2025,item]['source_label'],
                'before_usd':val(c,2024,item),'after_usd':val(c,2025,item),'change_usd':val(c,2025,item)-val(c,2024,item),
                'source_component_keys':[[c,y,item] for y in [2024,2025]]})
    assert len(checks)==33 and len(cash_bridges)==6
    # The grouped Visa OCF change includes every contributing row exactly once.
    operands={r['item'] for r in plan['companies']['visa']['rows'] if r['role'] in ['net_income','cash_adjustment','operating_balance_change']}
    groups={'net_income':['net_income'],
        'net_incentive_reconciliation':['client_incentives_adjustment','client_incentives_change'],
        'net_settlement_reconciliation':['settlement_receivable','settlement_payable'],
        'accrued_other_liabilities':['accrued_other_liabilities']}
    used={i for group in groups.values() for i in group};groups['remaining_reconciliation_items']=sorted(operands-used)
    deltas=complete_group_delta({i:val('visa',2023,i) for i in operands},{i:val('visa',2024,i) for i in operands},groups)
    assert sum(deltas.values())==val('visa',2024,'operating_cash_flow')-val('visa',2023,'operating_cash_flow')==-805000000
    cash_change={'company':'visa','before_year':2023,'after_year':2024,
        'before_ocf_usd':val('visa',2023,'operating_cash_flow'),'after_ocf_usd':val('visa',2024,'operating_cash_flow'),
        'delta_ocf_usd':sum(deltas.values()),'group_deltas_usd':deltas,'group_members':groups,
        'source_component_keys':[[ 'visa',y,i] for y in [2023,2024] for i in sorted(operands)],
        'limits':'Cash-flow reconciliation-row changes, not gross cash-payment totals. Supplemental tax cash is not added again.'}
    vcost=[r for r in cost_changes if r['company']=='visa']
    litigation=next(r for r in vcost if r['label']=='Litigation provision')
    personnel=next(r for r in vcost if r['label']=='Personnel')
    expense_delta=coreval('visa',2025,'operating_expenses')-coreval('visa',2024,'operating_expenses')
    assert sum(r['change_usd'] for r in vcost)==expense_delta==3675000000
    share=cost_change_share(litigation['change_usd'],expense_delta)
    op_bridge={'company':'visa','before_year':2024,'after_year':2025,
        'before_operating_income_usd':coreval('visa',2024,'operating_income'),
        'after_operating_income_usd':coreval('visa',2025,'operating_income'),
        'revenue_change_usd':coreval('visa',2025,'net_revenue')-coreval('visa',2024,'net_revenue'),
        'operating_expense_change_usd':expense_delta,'litigation_provision_change_usd':litigation['change_usd'],
        'personnel_change_usd':personnel['change_usd'],
        'other_operating_cost_change_usd':expense_delta-litigation['change_usd']-personnel['change_usd'],
        'litigation_share_of_expense_increase':display(share,'fraction'),
        'share_numerator':share.numerator,'share_denominator':share.denominator,
        'core_sources':[core['visa',y,i] for y in [2024,2025] for i in ['net_revenue','operating_expenses','operating_income']],
        'limits':'Reported expense attribution, not cash paid, normalized profit, a forecast or complete economic causality.'}
    assert op_bridge['before_operating_income_usd']+op_bridge['revenue_change_usd']-expense_delta==op_bridge['after_operating_income_usd']
    income_changes=[]
    for c in ['visa','mastercard']:
        delta=lambda i:coreval(c,2025,i)-coreval(c,2024,i)
        op=delta('operating_income');nonop=delta('pretax_income')-op;tax=delta('income_tax');ni=delta('net_income')
        assert op+nonop-tax==ni
        income_changes.append({'company':c,'operating_income_change_usd':op,'nonoperating_income_change_usd':nonop,
            'tax_expense_change_usd':tax,'net_income_change_usd':ni,'residual_usd':0,
            'source_rows':[core[c,y,i] for y in [2024,2025] for i in ['operating_income','pretax_income','income_tax','net_income']]})
    history=[]
    for a in annual:
        c,y=a['company'],a['fiscal_year']
        fields=['company','fiscal_year','period_start','period_end','operating_margin','net_margin','cash_conversion',
            'operating_cash_flow','net_income','capital_payments_defined','fcf_proxy','fcf_proxy_margin']
        history.append({k:a[k] for k in fields})
        assert a['fcf_proxy']==a['operating_cash_flow']-a['capital_payments_defined']
    result={'chapter':7,'status':'passed_profitability_cashflow_bridges','plan_sha256':sha(ROOT/'docs/07_bridge_plan.json'),
        'component_sha256':sha(ROOT/'reports/07_pdf_components.json'),'model_sha256':sha(ROOT/'models/payment_financials.sqlite'),
        'supplemental_pdf_cells':156,'statement_closure_and_core_anchor_checks':len(checks),'checks':checks,
        'cash_bridges':cash_bridges,'operating_cost_changes':cost_changes,'visa_fy2025_operating_income_bridge':op_bridge,
        'visa_fy2024_cash_change_bridge':cash_change,'net_income_change_bridges':income_changes,
        'tax_expense_vs_cash_context':tax_context,'five_year_metrics':history,
        'issuer_explanation_record':'references/07_issuer_explanations.json',
        'claim_layers':['Arithmetic reconciliations','Issuer-disclosed explanations','Unverified deeper causes/recurrence'],
        'limits':plan['limits'],'next_chapter':'08 Financial Risk Indicators'}
    (ROOT/'reports/07_analysis_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    with (ROOT/'data/processed/07_metric_history.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(history[0]));w.writeheader();w.writerows(history)
    bridge_export=[]
    for group,amount in deltas.items():
        bridge_export.append({'bridge':'visa_2023_to_2024_ocf','group':group,'change_usd':amount,
            'component_items':';'.join(groups[group]),'meaning':'Change in reported OCF reconciliation rows, not gross payments'})
    with (ROOT/'data/processed/07_bridge_changes.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(bridge_export[0]));w.writeheader();w.writerows(bridge_export)
    print(json.dumps({'component_cells':156,'closure_anchor_checks':len(checks),'cash_bridges':len(cash_bridges),
        'visa_expense_change_usd':expense_delta,'litigation_share':display(share,'fraction'),
        'visa_2024_ocf_group_deltas_usd':deltas,'net_income_changes':[{k:v for k,v in x.items() if k!='source_rows'} for x in income_changes]},indent=2))

if __name__=='__main__':main()
