# Source attribution and access

This independent educational analysis uses public filings by Visa Inc.
(CIK0001403161) and Mastercard Incorporated (CIK0001141391), SEC EDGAR
Submissions and Company Facts JSON, and annual-report PDF copies published by
the issuers through their Q4 CDN investor-relations hosts. No affiliation or
endorsement is implied. Company and SEC names are used as source identifiers.

Exact per-file URLs, retrieval timestamps, byte counts and SHA-256 hashes are
in [the source manifest](references/02_source_manifest.json). The SEC primary
filing URLs, accessions and filing dates are recorded in
[the filing inventory](docs/02_filing_inventory.csv).
Issuer PDFs are not described as byte-identical SEC HTML copies. PDF page
numbers refer to physical 1-based file pages, which can differ from printed pages.

The [SEC FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)
states that government-created material and EDGAR public filing content are
free to access and reuse. The
[Website Dissemination policy](https://www.sec.gov/about/privacy-information#dissemination)
requests appropriate source citation and distinguishes logos, artwork and
trademarks. We do not assign a CC BY license to these sources or assume issuer
branding and report artwork are freely reusable. Raw issuer PDFs stay in the
ignored local data folder; later public delivery should favor attributed
derived tables and links rather than republishing the branded report files.

Acquisition used HTTPS with certificate validation. SEC requests declared a
research user agent; the final CDN batch was limited to one transfer per second.
The [SEC developer guidance](https://www.sec.gov/about/developer-resources)
sets a maximum of 10 requests per second. We downloaded selected files only.
No browser security warning was bypassed and no API keys, user account or private credentials were
needed for the SEC public data APIs.

APIs change over time. Existing snapshots are verified locally, not silently
refreshed. The as-of date is the filing-selection cutoff; each source has its
own retrieval timestamp. New revisions or downloads need a separately versioned
snapshot and corresponding reconciliation.
