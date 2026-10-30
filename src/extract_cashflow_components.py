from reproducibility import snapshot,check_snapshot
"""Read supplemental PDF components independently; no live API or source edits."""
import csv
import json
from financial_model import ROOT,sha,load
from cashflow_bridge import parse_row
from financial_reconciliation import canonical_usd

def main():
    import pdfplumber
    protected=snapshot()
    check_snapshot(protected)
    plan=load('docs/07_bridge_plan.json');rows=[]
    manifest={x['path']:x for x in load('references/02_source_manifest.json')['files']}
    for company,config in plan['companies'].items():
        pdf=ROOT/config['pdf_path'];source=manifest[config['pdf_path']]
        with pdfplumber.open(pdf) as doc:
            pages={page:doc.pages[page-1].extract_text().splitlines() for page in {x['pdf_page'] for x in config['rows']}}
            for spec in config['rows']:
                lines=pages[spec['pdf_page']]
                assert any('2025' in s and '2024' in s and '2023' in s for s in lines),'Missing declared year-column header'
                parsed=parse_row(lines,spec['label'],spec['section'])
                for year,token,amount in zip(config['column_years'],parsed['tokens'],parsed['amounts']):
                    value=canonical_usd(amount,spec['normalization'])
                    if spec['role'] in ['operating_cost','capital_payment','cash_tax']:assert value>=0
                    rows.append({'company':company,'fiscal_year':year,'item':spec['item'],'role':spec['role'],
                        'section':spec['section'],'source_label':parsed['label'],'source_token':token,
                        'source_signed_amount_usd':int(amount*plan['scale_to_usd']),'amount_usd':value,
                        'normalization':spec['normalization'],'report_unit':plan['unit'],
                        'pdf_path':config['pdf_path'],'pdf_page':spec['pdf_page'],'pdf_line_index':parsed['line_index'],
                        'pdf_sha256':source['sha256'],'source_url':source['url'],
                        'publication_accession':config['publication_accession'],
                        'source_basis':'Direct PDF row; closure/core anchors checked separately; not an individual API-match claim.'})
    assert len(rows)==156 and len({(r['company'],r['fiscal_year'],r['item']) for r in rows})==156
    result={'chapter':7,'status':'extracted_supplemental_pdf_components','plan_sha256':sha(ROOT/'docs/07_bridge_plan.json'),
        'rows':rows,'component_cells':len(rows),'numeric_pdf_pages':len({(r['pdf_path'],r['pdf_page']) for r in rows}),
        'scope':'FY2023–2025 components from two frozen FY2025 PDFs; source figures are not individually API-reconciled.'}
    (ROOT/'reports/07_pdf_components.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    with (ROOT/'data/processed/07_statement_components.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print('Extracted156 PDF cells on5 statement/tax pages; no source refresh.')

if __name__=='__main__':main()
