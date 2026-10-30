"""Extract declared PDF rows independently of all Company Facts amounts."""
import csv
import json
import hashlib
import importlib.metadata
from pathlib import Path
import pdfplumber
from prepare_sources import ROOT,sha,verify
from financial_reconciliation import match_pdf_row,canonical_usd,applicable,csv_cell

def main():
    sources=verify()
    plan=json.loads((ROOT/'docs/03_reconciliation_plan.json').read_text())
    pdfs=json.loads((ROOT/'references/source_catalog.json').read_text())['pdf_review']['pdfs']
    rows=[];pages=[]
    for pdf in pdfs:
        print('Extracting source rows: '+pdf['key'],flush=True)
        with pdfplumber.open(ROOT/pdf['path']) as document:
            for statement in ['operations','balance','cashflow']:
                pno=pdf['page_locations_1_based'][statement]
                page=document.pages[pno-1]
                lines=page.extract_text(x_tolerance=2,y_tolerance=3).splitlines()
                boxes=page.extract_text_lines(x_tolerance=2,y_tolerance=3,layout=False)
                assert [b['text'] for b in boxes]==lines,'Text and rendered-row locators differ'
                years=pdf['statement_year_columns'][statement]
                page_rows=[]
                for item in plan['items']:
                    if item['statement']!=statement or pdf['company'] not in item['companies']:continue
                    targets=[y for y in years if applicable(item,pdf['company'],y)]
                    if not targets:continue
                    # Optional historical temporary-equity component is absent
                    # from later face statements: do not fabricate a zero row.
                    if item['id']=='redeemable_noncontrolling_interests' and pdf['fiscal_year']==2025:continue
                    row=match_pdf_row(lines,item['label_aliases'],len(years))
                    bb=[boxes[i] for i in row['line_indices']]
                    bbox=[min(b['x0'] for b in bb),min(b['top'] for b in bb),max(b['x1'] for b in bb),max(b['bottom'] for b in bb)]
                    for year in targets:
                        col=years.index(year);signed=row['amounts'][col]*plan['report_scale_to_usd']
                        record={'company':pdf['company'],'data_fiscal_year':year,'item':item['id'],
                            'period_type':item['period_type'],'publication_fiscal_year':pdf['fiscal_year'],
                            'pdf_path':pdf['path'],'pdf_sha256':pdf['sha256'],'pdf_page':pno,
                            'statement':statement,'year_columns':years,'selected_column_zero_based':col,
                            'source_label':row['source_label'],'source_label_contains_nul':'\x00' in row['source_label'],
                            'source_token':row['tokens'][col],'source_signed_amount_usd':int(signed),
                            'canonical_amount_usd':canonical_usd(row['amounts'][col],item['normalization'],plan['report_scale_to_usd']),
                            'normalization':item['normalization'],'report_unit':'USD millions','source_row_bbox':bbox,
                            'explicit_reported_nil':row['tokens'][col].strip() in ['—','–','-']}
                        rows.append(record);page_rows.append(record)
                pages.append({'pdf_path':pdf['path'],'pdf_sha256':pdf['sha256'],'page':pno,
                    'statement':statement,'year_columns':years,'rows':page_rows,
                    'extracted_text_sha256':hashlib.sha256('\n'.join(lines).encode()).hexdigest()})
    result={'scope':'Independent PDF row extraction; this program never opens Company Facts amounts',
        'plan_sha256':sha(ROOT/'docs/03_reconciliation_plan.json'),'pdfplumber_version':importlib.metadata.version('pdfplumber'),
        'rows':rows,'pages':pages,'source_rows':len(rows),'source_pdf_pages':len(pages),
        'raw_source_files_unchanged':all(sha(ROOT/e['path'])==e['sha256'] for e in sources['files'])}
    (ROOT/'reports/03_pdf_statement_values.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    fields=['company','data_fiscal_year','item','publication_fiscal_year','pdf_path','pdf_sha256','pdf_page',
        'source_label','source_token','source_signed_amount_usd','canonical_amount_usd','normalization','explicit_reported_nil']
    with (ROOT/'reports/03_pdf_statement_values.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        writer.writerows({k:csv_cell(r[k]) for k in fields} for r in rows)
    print(f'Extracted {len(rows)} item/year observations from {len(pages)} source pages.')

if __name__=='__main__':main()
