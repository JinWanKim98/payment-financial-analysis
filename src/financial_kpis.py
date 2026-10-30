"""Evaluate approved KPI expressions with exact values and leaf-source lineage."""
import csv
import json
from datetime import date,timedelta
from decimal import Decimal,localcontext,ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def read_contract():
    return json.loads((ROOT/'docs/04_finance_kpi_dictionary.json').read_text())

def read_inputs(path=None):
    with Path(path or ROOT/'data/processed/03_financial_items.csv').open(newline='') as f:
        return list(csv.DictReader(f))

def periods(contract,company,year):
    c=contract['companies'][company]
    return f"{year+c['start_year_offset']}-{c['start_month_day']}",f"{year}-{c['fiscal_year_end']}"

def unique_lineage(results):
    rows={}
    for result in results:
        for row in result['lineage']:
            key=(row['company'],int(row['data_fiscal_year']),row['item'])
            if key in rows and rows[key]!=row:raise ValueError('Conflicting lineage: '+str(key))
            rows[key]=row
    return [rows[k] for k in sorted(rows)]

def result(value=None,reasons=(),lineage=(),status=None):
    return {'value':value,'status':status or ('defined' if value is not None else 'undefined'),
        'reasons':sorted(set(reasons)),'lineage':list(lineage)}

class KPIEngine:
    def __init__(self,contract,rows):
        self.contract=contract
        self.metrics={m['id']:m for m in contract['metrics']}
        if len(self.metrics)!=len(contract['metrics']):raise ValueError('Duplicate metric ID')
        self.rows={}
        for row in rows:
            key=(row['company'],int(row['data_fiscal_year']),row['item'])
            if key in self.rows:raise ValueError('Duplicate input key: '+str(key))
            self.rows[key]=dict(row)
        self.cache={}

    def source(self,item,company,year):
        row=self.rows.get((company,year,item))
        if row is None:return result(reasons=[f'missing_input:{company}:{year}:{item}'])
        # Exact year/company/date checks stop accidental quarter, YTD or peer joins.
        start,end=periods(self.contract,company,year)
        source_types={x['item']:x['period_type'] for m in self.metrics.values() for x in m['source_mapping'] if 'item' in x}
        expected=source_types.get(item)
        if (row['company']!=company or int(row['data_fiscal_year'])!=year or
            row['period_end']!=end or row['period_type']!=expected or
            row['period_start']!=(start if expected=='duration' else '')):
            return result(reasons=['invalid_period_context:'+item],lineage=[row])
        if row['api_unit']!='USD':return result(reasons=['invalid_input_unit:'+item],lineage=[row])
        if row['status']!='matched' or int(row['difference_usd'])!=0:
            return result(reasons=['unreconciled_input:'+item],lineage=[row])
        try:
            amount=Fraction(row['canonical_amount_usd'])
            if amount.denominator!=1 or amount!=Fraction(row['api_value_usd']):raise ValueError()
        except (ValueError,ZeroDivisionError):return result(reasons=['invalid_source_amount:'+item],lineage=[row])
        if item in self.contract['nonnegative_source_items'] and amount<0:
            return result(reasons=['invalid_negative_source:'+item],lineage=[row])
        return result(amount,lineage=[row])

    def evaluate(self,metric_id,company,year,stack=()):
        if metric_id not in self.metrics:raise ValueError('Unknown metric: '+metric_id)
        if company not in self.contract['companies']:raise ValueError('Unknown company: '+company)
        if type(year) is not int:raise ValueError('Fiscal year must be an integer')
        if year not in self.contract['target_fiscal_years']:
            return result(reasons=['outside_verified_fiscal_years'])
        key=(metric_id,company,year)
        if key in stack:raise ValueError('Cyclic KPI dependency')
        if key in self.cache:return self.cache[key]
        m=self.metrics[metric_id];calc=m['calculation'];op=calc['op']
        if op=='industry_evidence':
            from industry_metrics import verified_rows
            row=next((r for r in verified_rows() if (r['metric_id'],r['company'],r['fiscal_year'])==key),None)
            out=result(Fraction(row['value']),lineage=[dict(row,item=metric_id,data_fiscal_year=year)]) if row else result(reasons=['missing_industry_evidence'])
            self.cache[key]=out
            return out
        if m['status']=='deferred_source_mapping':
            out=result(reasons=['source_mapping_pending:'+metric_id],status='deferred')
            self.cache[key]=out;return out
        def resolve(ref,y=year):
            if set(ref)=={'item'}:return self.source(ref['item'],company,y)
            if set(ref)=={'metric'}:return self.evaluate(ref['metric'],company,y,stack+(key,))
            raise ValueError('Invalid typed operand: '+str(ref))
        if op=='reported':operands=[resolve(calc['arg'])]
        elif op in ['sum','subtract','ratio']:operands=[resolve(ref) for ref in calc['args']]
        elif op=='sum_by_company':operands=[resolve(ref) for ref in calc['terms_by_company'][company]]
        elif op=='growth':operands=[resolve({'item':calc['item']},y) for y in [year,year-1]]
        elif op=='pp_change':operands=[resolve({'metric':calc['metric']},y) for y in [year,year-1]]
        else:raise ValueError('Unsupported operation: '+op)
        lineage=unique_lineage(operands)
        reasons=[reason for x in operands for reason in x['reasons']]
        if any(x['value'] is None for x in operands):
            if op in ['growth','pp_change'] and year-1 not in self.contract['target_fiscal_years']:
                reasons.append('missing_verified_prior_year')
            out=result(reasons=reasons,lineage=lineage)
        else:
            values=[x['value'] for x in operands]
            if op in ['growth','pp_change']:
                previous_start,previous_end=periods(self.contract,company,year-1)
                current_start,current_end=periods(self.contract,company,year)
                if date.fromisoformat(previous_end)+timedelta(days=1)!=date.fromisoformat(current_start):
                    raise ValueError('Nonconsecutive fiscal periods')
            if op in ['ratio','growth'] and values[1]<=0:
                out=result(reasons=['nonpositive_denominator' if op=='ratio' else 'nonpositive_growth_base'],lineage=lineage)
            else:
                if op=='reported':value=values[0]
                elif op in ['sum','sum_by_company']:value=sum(values,Fraction(0))
                elif op=='subtract':value=values[0]-values[1]
                elif op=='ratio':value=values[0]/values[1]
                elif op=='growth':value=values[0]/values[1]-1
                elif op=='pp_change':value=100*(values[0]-values[1])
                out=result(value,lineage=lineage)
        self.cache[key]=out
        return out

def display(value,unit):
    if value is None:return 'N/A'
    with localcontext() as ctx:
        ctx.prec=50
        x=Decimal(value.numerator)/Decimal(value.denominator)
        if unit=='fraction':x*=100;suffix='%'
        elif unit=='percentage_points':suffix=' pp'
        elif unit=='multiple':suffix='x'
        elif unit=='USD':x/=1000000;suffix=' USD million'
        elif unit=='count':x/=1000000;suffix=' million transactions'
        else:raise ValueError('Unsupported ready metric unit: '+unit)
        return format(x.quantize(Decimal('.01'),rounding=ROUND_HALF_UP),',.2f')+suffix

def serialize(engine,metric_id,company,year):
    m=engine.metrics[metric_id];r=engine.evaluate(metric_id,company,year)
    start,end=periods(engine.contract,company,year)
    value=r['value']
    with localcontext() as ctx:
        ctx.prec=28
        decimal=None if value is None else str(Decimal(value.numerator)/Decimal(value.denominator))
    return {'metric_id':metric_id,'label_en':m['label_en'],
        'company':company,'fiscal_year':year,'period_type':'instant' if m['period_rule']=='instant_same_company' else 'duration' if m['status']=='ready' else 'unmapped',
        'period_start':None if m['period_rule']=='instant_same_company' or m['status']!='ready' else start,
        'period_end':end,'basis':m['basis'],'unit':m['storage_unit'],
        'value_numerator':None if value is None else value.numerator,
        'value_denominator':None if value is None else value.denominator,
        'value_decimal':decimal,'display':display(value,m['storage_unit']) if value is not None else 'N/A',
        'status':r['status'],'reasons':r['reasons'],'lineage':r['lineage'],
        'peer_warning':m['peer_policy'],'definition_warning':m['limitations']}

def source_terms(calc,metrics,company):
    """Expand typed operands to leaf items for a specific issuer, independent of values."""
    op=calc['op']
    if op in ['deferred','industry_evidence']:return set()
    refs=([calc['arg']] if op=='reported' else calc.get('args',[]))
    if op=='sum_by_company':refs=calc['terms_by_company'][company]
    if op=='growth':refs=[{'item':calc['item']}]
    if op=='pp_change':refs=[{'metric':calc['metric']}]
    found=set()
    for ref in refs:
        if 'item' in ref:found.add(ref['item'])
        else:found.update(source_terms(metrics[ref['metric']]['calculation'],metrics,company))
    return found
