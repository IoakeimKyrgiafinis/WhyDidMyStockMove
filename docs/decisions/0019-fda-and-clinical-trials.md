# 0019: FDA decisions and clinical-trial news

What, for the universe's health care companies (375 in November 2026):

- New drugs and new uses approved: NDA or BLA originals and efficacy supplements from
  Drugs@FDA. Generics (ANDAs), labeling, manufacturing and REMS changes are routine and left out.
- Device approvals: premarket approval (PMA) originals. Supplements (about 2,500 a year) are
  routine and left out; 510(k) clearances are too many and too minor.
- Class I recalls, the most serious class, one per recall event.
- Phase 3 results first posted, and Phase 3 trials terminated, suspended or withdrawn (with the
  sponsor's reason), from ClinicalTrials.gov.

Sources: openFDA's API (no key: 240 requests a minute, 1,000 a day; a run makes about 30, using
date-range searches instead of the bulk files, which for device recalls are 91 MB zipped) and
the ClinicalTrials.gov API (one request per company, a second apart). The whole 800-day window is
read again every night, in about seven minutes. A source that fails keeps last night's rows.

Matching companies: FDA abbreviates sponsor names ("BAXTER HLTHCARE", "VERTEX PHARMS"). A
sponsor matches a company when its name starts with the company's name, or when the first word
is the same, at least five letters long and not a common word ("American", "United"). When
several companies share that first word, the second word decides, allowing FDA's
abbreviations ("ACADIA PHARMS" is Acadia Pharmaceuticals, not Acadia Healthcare); if it cannot,
the sponsor is left unmatched. Approvals filed under a subsidiary with another name (Janssen for
Johnson & Johnson) are missed. On a real run, the 60 most frequent drug sponsors and 30 device
applicants all matched the right company.

Timing: these sources give a date but no time of day, so each event counts from the session
after its date, the same rule as insider filings; it gives up the day's reaction rather than
risk look-ahead. The dates are FDA's action date, FDA's weekly enforcement report, and
ClinicalTrials.gov's posting dates (a stopped trial is dated by the record update that shows it).

Use: listed in the news view; not used in any statistic.
