"""Acquire or verify a frozen SEC API / issuer PDF source snapshot.

Once a manifest exists, acquisition becomes verification and never refreshes
live APIs silently. Adoption registers successful staged transfers unchanged.
"""
import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'references/02_source_manifest.json'
PDF_URLS={
 'visa_2025':'https://s29.q4cdn.com/385744025/files/doc_downloads/2025/Visa-Fiscal-2025-Annual-Report.pdf',
 'visa_2023':'https://s29.q4cdn.com/385744025/files/doc_downloads/2023/Visa-Inc-Fiscal-2023-Annual-Report.pdf',
 'visa_2021':'https://s29.q4cdn.com/385744025/files/doc_downloads/Visa-Inc_-Fiscal-2021-Annual-Report.pdf',
 'mastercard_2025':'https://s25.q4cdn.com/479285134/files/doc_financials/2025/q4/MA-12-31-2025-10-K-as-filed-with-standard-exhibits.pdf',
 'mastercard_2023':'https://s25.q4cdn.com/479285134/files/doc_financials/2023/AR/2023-annual-report.pdf',
 'mastercard_2021':'https://s25.q4cdn.com/479285134/files/doc_financials/2021/q4/MA.12.31.2021-10-K-as-filed-Exhibits.pdf',
}

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def source_specs():
    specs=[]
    for slug,cik in [('visa','0001403161'),('mastercard','0001141391')]:
        for kind,url in [('submissions','https://data.sec.gov/submissions/CIK'+cik+'.json'),
                         ('companyfacts','https://data.sec.gov/api/xbrl/companyfacts/CIK'+cik+'.json')]:
            specs.append({'key':slug+'_'+kind,'company':slug,'kind':kind,'cik':cik,'url':url,
                'path':f'data/raw/sec/{slug}/{kind}.json','staged':f'data/raw/{slug}_{kind}.json.part'})
    for key,url in PDF_URLS.items():
        slug,year=key.split('_')
        specs.append({'key':key,'company':slug,'kind':'issuer_annual_pdf','fiscal_year':int(year),
            'url':url,'path':f'data/raw/issuer_reports/{slug}/FY{year}_annual_report.pdf',
            'staged':f'data/raw/{key}_annual_report.pdf.part'})
    return specs

def validate_payload(path,spec):
    if spec['kind']=='issuer_annual_pdf':
        with path.open('rb') as stream:assert stream.read(5)==b'%PDF-',spec['key']
        assert path.stat().st_size>100000,spec['key']
    else:
        data=json.loads(path.read_text())
        assert str(data['cik']).zfill(10)==spec['cik'],spec['key']
        assert ('filings' if spec['kind']=='submissions' else 'facts') in data,spec['key']

def verify():
    manifest=json.loads(MANIFEST.read_text())
    assert len(manifest['files'])==10
    for entry in manifest['files']:
        p=ROOT/entry['path']
        assert p.stat().st_size==entry['bytes'] and sha(p)==entry['sha256'],entry['path']
        validate_payload(p,entry)
    return manifest

def acquire(adopt=False):
    if MANIFEST.exists():
        manifest=verify();print('Existing10-file snapshot verified; no network refresh.');return manifest
    entries=[]
    for spec in source_specs():
        staged=ROOT/spec['staged'];dest=ROOT/spec['path']
        if dest.exists():
            # Resume after a previous adoption was interrupted.
            validate_payload(dest,spec)
        else:
            if not adopt:
                assert not staged.exists(),'Staged file exists; validate/adopt it or choose a new snapshot'
                staged.parent.mkdir(parents=True,exist_ok=True)
                header=ROOT/'data/work'/('02_'+spec['key']+'_fetch.headers')
                subprocess.run(['curl','--fail','--location','--silent','--show-error',
                    '--connect-timeout','10','--max-time','60','--dump-header',str(header),
                    '-A',os.environ.get('SEC_USER_AGENT','PortfolioResearch/1.0 (individual educational financial analysis)'),
                    '-o',str(staged),spec['url']],check=True)
                time.sleep(1)
            assert staged.exists(),spec['staged']
            validate_payload(staged,spec)
            dest.parent.mkdir(parents=True,exist_ok=True)
            assert not dest.exists()
            staged.rename(dest)
        record={k:v for k,v in spec.items() if k!='staged'}
        record.update(bytes=dest.stat().st_size,sha256=sha(dest),
            retrieved_at_utc=datetime.fromtimestamp(dest.stat().st_mtime,timezone.utc).isoformat(),
            retrieval_time_basis='Local write-completion mtime of the successful transfer; registration does not alter it',
            transport='HTTPS; certificate validation enabled')
        if spec['kind']=='issuer_annual_pdf':
            record['representation']='Issuer-published PDF containing Form10-K; not byte-identical to SEC HTML'
        entries.append(record)
    result={'snapshot':'project03-2026-10-29','as_of_date':'2026-10-29',
        'registered_at_utc':datetime.now(timezone.utc).isoformat(),'files':entries,
        'api_refresh_policy':'Existing snapshot is verify-only; use a separately versioned snapshot to refresh',
        'transport_exception':'SEC HTML curl returned403; six issuer-published PDF copies were acquired. System DNS failure for s25.q4cdn.com was resolved with a recorded public DNS response; HTTPS certificate checking stayed enabled.',
        'raw_sec_html_acquired':False,'financial_values_reconciled':False,
        'snapshot_not_instantaneous':'Per-file retrieval timestamps differ; as_of_date is the filing-selection cutoff, not a claim of simultaneous downloads.'}
    MANIFEST.write_text(json.dumps(result,indent=2)+'\n')
    print('Registered10 source files without changing their bytes.')
    return verify()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--verify-existing',action='store_true')
    mode.add_argument('--fetch',action='store_true')
    mode.add_argument('--adopt-downloads',action='store_true')
    args=parser.parse_args()
    if args.verify_existing:
        m=verify();print('Verified',len(m['files']),'unchanged source files.')
    else:acquire(args.adopt_downloads)
