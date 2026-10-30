"""Small, testable descriptive calculations. No forecast or causal inference."""
import math
from fractions import Fraction
from financial_kpis import read_contract,periods

def growth(current,previous):
    if current is None or previous is None or previous<=0:return None
    return Fraction(current,previous)-1

def rebase(values):
    if not values or values[0] is None or values[0]<=0:return [None for _ in values]
    return [None if x is None else 100*Fraction(x,values[0]) for x in values]

def cagr(first,last,intervals):
    if type(intervals) is not int or intervals<=0:raise ValueError('Positive integer interval count required')
    if first is None or last is None or first<=0 or last<=0:return None
    return (last/first)**(1/intervals)-1

def annual_series(records,company):
    d=read_contract()
    if company not in d['companies']:raise ValueError('Unknown company')
    rows=sorted([x for x in records if x['company']==company],key=lambda x:x['fiscal_year'])
    years=[x['fiscal_year'] for x in rows]
    if years!=d['target_fiscal_years']:raise ValueError('Expected five distinct, consecutive fiscal years')
    for r in rows:
        if (r['period_start'],r['period_end'])!=periods(d,company,r['fiscal_year']):
            raise ValueError('Fiscal-period mismatch')
        for item in ['net_revenue','operating_income','operating_expenses','net_income','operating_cash_flow']:
            if type(r[item]) is not int:raise ValueError('Exact integer USD inputs required')
    return rows

def summarize(records):
    out={}
    for company in read_contract()['companies']:
        r=annual_series(records,company);first,last,prior=r[0],r[-1],r[-2]
        intervals=last['fiscal_year']-first['fiscal_year']
        out[company]={
            'first_fiscal_year':2021,'last_fiscal_year':2025,'growth_intervals':intervals,
            'first_revenue_usd':first['net_revenue'],'last_revenue_usd':last['net_revenue'],
            'cumulative_revenue_growth':float(growth(last['net_revenue'],first['net_revenue'])),
            'revenue_cagr':cagr(first['net_revenue'],last['net_revenue'],intervals),
            'last_revenue_index_2021_100':float(rebase([x['net_revenue'] for x in r])[-1]),
            'fy2025_revenue_growth':float(growth(last['net_revenue'],prior['net_revenue'])),
            'fy2025_operating_income_growth':float(growth(last['operating_income'],prior['operating_income'])),
            'fy2025_operating_expense_growth':float(growth(last['operating_expenses'],prior['operating_expenses'])),
            'fy2025_operating_margin':last['operating_margin'],
            'fy2025_operating_margin_change_pp':100*(last['operating_margin']-prior['operating_margin']),
            'fy2024_ocf_growth':float(growth(prior['operating_cash_flow'],r[-3]['operating_cash_flow'])),
            'fy2024_net_income_growth':float(growth(prior['net_income'],r[-3]['net_income'])),
            'cash_conversion_min':min(x['cash_conversion'] for x in r),
            'cash_conversion_max':max(x['cash_conversion'] for x in r),
            'annual_ocf_above_consolidated_net_income_count':sum(x['operating_cash_flow']>x['net_income'] for x in r),
            'endpoint_source_keys':[[company,y,'net_revenue'] for y in [2021,2025]],
            'fy2025_change_source_keys':[[company,y,i] for y in [2024,2025] for i in ['net_revenue','operating_income','operating_expenses']],
            'cash_source_keys':[[company,x['fiscal_year'],i] for x in r for i in ['net_income','operating_cash_flow']],
            'interpretation':'Reported own-fiscal-period description. Reasons and sustainability are not established.'}
    return out
