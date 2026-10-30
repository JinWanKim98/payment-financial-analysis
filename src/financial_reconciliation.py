"""Exact source-item matching. No ratios, trends or company scores."""
import re
import unicodedata
from decimal import Decimal,InvalidOperation

TOKEN=re.compile(r'(?<!\S)(?:\$\s*)?(?:\(?[-−]?\d[\d,]*(?:\.\d+)?\)?|[—–-])(?=\s|$)')

def label_key(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalpha())

def parse_amount(token):
    token=token.replace('$','').strip()
    if token in ['—','–','-']:return Decimal(0)
    assert '\x00' not in token,'Never repair a digit glyph'
    negative=token.startswith('(')
    if negative:
        assert token.endswith(')') and not token[1:].startswith(('-','−')),'Malformed sign'
        token=token[1:-1]
    else:assert not token.endswith(')')
    if ',' in token:
        assert re.fullmatch(r'[-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?',token),'Malformed thousands'
    token=token.replace(',','').replace('−','-')
    value=Decimal(token)
    assert value.is_finite()
    return -value if negative else value

def split_numeric_line(text,columns):
    matches=list(TOKEN.finditer(text))
    if len(matches)<columns:return None
    matches=matches[-columns:]
    prefix=text[:matches[0].start()].rstrip(' .$')
    if not prefix or re.search(r'\d',prefix):return None
    # Require all suffix content to consist of the exact financial tokens.
    remainder=text[matches[0].start():]
    if TOKEN.sub('',remainder).strip():return None
    return prefix,[m.group().strip() for m in matches]

def match_pdf_row(lines,aliases,columns):
    wanted={label_key(a) for a in aliases};matches=[]
    for index,line in enumerate(lines):
        split=split_numeric_line(line,columns)
        if split is None:continue
        prefix,tokens=split
        candidates=[(prefix,[index])]
        if index>0:candidates.append((lines[index-1]+' '+prefix,[index-1,index]))
        for candidate,indices in candidates:
            if label_key(candidate) in wanted:
                matches.append({'source_label':candidate,'tokens':tokens,'line_indices':indices,
                    'amounts':[parse_amount(t) for t in tokens]});break
    assert len(matches)==1,('Missing or ambiguous PDF row',aliases,len(matches))
    return matches[0]

def canonical_usd(pdf_amount,normalization,scale=1000000):
    value=pdf_amount*scale
    if normalization=='payment_magnitude':
        assert value<=0,'Cash-payment row unexpectedly positive; do not blindly take abs'
        value=-value
    else:assert normalization=='signed'
    assert value==value.to_integral_value(),'Non-integral USD amount'
    return int(value)

def api_candidates(facts,tags,accession,start,end,period_type):
    assert period_type in ['duration','instant']
    if period_type=='duration':assert start is not None
    else:assert start is None
    found=[]
    for tag in tags:
        for index,r in enumerate(facts.get('facts',{}).get('us-gaap',{}).get(tag,{}).get('units',{}).get('USD',[])):
            if r.get('accn')!=accession or r.get('end')!=end or r.get('form')!='10-K':continue
            if r.get('fp')!='FY':continue
            if (period_type=='duration' and r.get('start')!=start) or (period_type=='instant' and r.get('start') is not None):continue
            assert type(r.get('val')) in [int,float] and Decimal(str(r['val'])).is_finite(),'Invalid API number'
            found.append({'tag':tag,'json_pointer':f'/facts/us-gaap/{tag}/units/USD/{index}',**r})
    return found

def resolve_api(facts,tags,accession,start,end,period_type,filed_date):
    found=api_candidates(facts,tags,accession,start,end,period_type)
    assert found,('No exact API observation',tags,accession,start,end,period_type)
    assert {x['filed'] for x in found}=={filed_date},'Filed metadata mismatch'
    values={Decimal(str(x['val'])) for x in found}
    assert len(values)==1,('Conflicting observations in the same context',found)
    value=next(iter(values));assert value==value.to_integral_value()
    return {'value_usd':int(value),'records':found,'duplicate_consistent_records':len(found)-1}

def applicable(item,company,year):
    return company in item['companies'] and year in item.get('applicable_years',range(2021,2026))

def item_tags(item,company,publication_fy):
    overrides=item.get('tag_by_publication_fy',{}).get(company)
    if overrides is not None:
        assert str(publication_fy) in overrides,'Unreviewed publication-year tag mapping'
        return overrides[str(publication_fy)]
    return item['tags'][company]

def csv_cell(value):
    # Retain raw glyphs in JSON evidence; make CSV labels safe for spreadsheet
    # readers without modifying amounts or silently repairing source words.
    return value.replace('\x00',r'\u0000') if isinstance(value,str) else value


def read_sql(name):
    """Read one named SQL section from analysis.sql."""
    from pathlib import Path
    import re
    path = Path(__file__).resolve().parents[1] / 'analysis.sql'
    sections = re.split(r'^-- BEGIN FILE: (.+)$', path.read_text(), flags=re.MULTILINE)
    queries = {sections[i].strip(): sections[i + 1] for i in range(1, len(sections), 2)}
    if name not in queries:
        raise KeyError('Unknown SQL section: ' + name)
    return queries[name]
