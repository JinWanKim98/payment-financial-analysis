"""Section-aware source rows and complete cash/profit reconciliations."""
import re
from financial_reconciliation import TOKEN,label_key,parse_amount,canonical_usd

def select_section(lines,section):
    if section=='all':return lines,0
    if section=='cash_tax_table':
        a=next(i for i,s in enumerate(lines) if s.strip()=='Income Taxes Paid')
        b=next(i for i,s in enumerate(lines) if s.strip()=='Deferred Income Taxes')
        return lines[a+1:b],a+1
    a=next(i for i,s in enumerate(lines) if s.startswith('Adjustments to reconcile'))
    b=next(i for i,s in enumerate(lines) if s.startswith(('Change in operating assets','Changes in operating assets')))
    c=next(i for i,s in enumerate(lines) if s.startswith('Net cash provided by'))
    if section=='adjustments':return lines[a+1:b],a+1
    if section=='operating_changes':return lines[b+1:c],b+1
    raise ValueError('Unknown section: '+section)

def parse_row(lines,label,section='all',columns=3):
    lines,offset=select_section(lines,section);found=[]
    for index,text in enumerate(lines):
        matches=list(TOKEN.finditer(text))
        if len(matches)<columns:continue
        cells=matches[-columns:];prefix=text[:cells[0].start()].rstrip(' .$')
        if label_key(prefix)!=label_key(label):continue
        clean=re.sub(r'\(1\)','',prefix)
        if re.search(r'\d',clean):raise ValueError('Unexpected digit in matched row label')
        if TOKEN.sub('',text[cells[0].start():]).strip():raise ValueError('Unexpected numeric suffix')
        tokens=[x.group().strip() for x in cells]
        found.append({'label':prefix,'line_index':index+offset,'tokens':tokens,'amounts':[parse_amount(t) for t in tokens]})
    if len(found)!=1:raise ValueError(f'Expected exactly one row for {label} / {section}; found {len(found)}')
    return found[0]

def close_bridge(net_income,adjustments,balance_changes,reported_ocf):
    if any(x is None for x in [net_income,reported_ocf,*adjustments,*balance_changes]):
        raise ValueError('Missing cash-flow operand is not zero')
    computed=net_income+sum(adjustments)+sum(balance_changes)
    if computed!=reported_ocf:raise ValueError(f'Cash bridge residual: {computed-reported_ocf}')
    return {'net_income':net_income,'adjustment_total':sum(adjustments),
        'operating_balance_change_total':sum(balance_changes),'operating_cash_flow':reported_ocf,'residual_usd':0}

def complete_group_delta(before,after,groups):
    if set(before)!=set(after):raise ValueError('Different component coverage between periods')
    members=[item for group in groups.values() for item in group]
    if len(members)!=len(set(members)):raise ValueError('Overlapping groups double-count components')
    if set(members)!=set(before):raise ValueError('Groups must cover every operand exactly once')
    return {name:sum(after[i]-before[i] for i in items) for name,items in groups.items()}

def cost_change_share(component_change,total_change):
    from fractions import Fraction
    if total_change<=0:return None
    return Fraction(component_change,total_change)
