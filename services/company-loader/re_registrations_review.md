# The 25 uncertain re-registrations worth a look: review (2026-11-17)

`re_registrations_uncertain.csv`'s `for_the_owner` rows, checked read-only on hermes: stored
submissions (names and former names with dates, filings around the move, report spans), every
shared pre-move fact of the two CIKs (all metrics, exact and at the successor's printed rounding),
and the successor's own stored filings' words about the predecessor. Linked = appended to
`src/company_loader/data/re_registrations.csv` (the existing rule: the successor's pre-move figures
equal the predecessor's, or a documented holding-company reorganisation of one company, no merger
partner). Pre-XBRL links move filings only (no figures before 2009).

| Successor | Candidate | Verdict | Why |
|---|---|---|---|
| CI Cigna Group | Cigna Holding Co (old Cigna) | linked | 108 of 121 shared pre-move facts equal (89%); the rest its 2018 10-K's revenue restated 0.1-1.8%; renamed the same day; same address and phone (the 6-metric test read 26 of 37 because of those restatements) |
| VNOM Viper Energy | VNOM Sub (old Viper) | linked | 118 of 128 equal at the successor's printed millions (92%), 48 of 53 core; exact equality read 80 of 128 only because it prints millions; Sitio was the acquired side |
| FUN Six Flags (new) | Six Flags/OLD | not linked | already company 0000701374, and the acquired side (63 of 88) |
| FUN Six Flags (new) | Cedar Fair L.P. (0000811532, found reviewing FUN) | linked | 131 of 139 equal, 34 of 34 core: the accounting predecessor; 8-K at the move, 15-12G after, no company here |
| ORCL Oracle | Oracle Systems (old Oracle) | linked | holding-company reorganisation in its own filings ("Oracle Systems Corporation (formerly known as Oracle Corporation)", "formerly known as Ozark Holding Inc."), both renamed 2006-02-01; reports stop at the move |
| ACN Accenture plc | Accenture Ltd | linked | its 10-K: "Accenture Ltd, our predecessor holding company, became a wholly owned subsidiary" (2009-09-01) |
| NBR Nabors Ltd | Nabors Industries Inc | linked | 2002 inversion: the reorganisation's merger agreement in its exhibits, NII its wholly owned subsidiary; 15-12B after |
| IDCC InterDigital | InterDigital Communications Corp | linked | 2007 holding-company reorganisation: assignment of the predecessor's agreements 2007-07-02, 8-K12B 2007-07-09, reports stop |
| RIG Transocean Ltd | Transocean Inc | linked | 2008 redomestication: parent of its wholly owned Transocean Inc., supplemental indenture 2008-12-18; the predecessor's last report is its 2008 10-K |
| BNTC Benitec Inc | Benitec Biopharma Ltd | linked | its 8-K: "the re-domiciliation of Benitec Biopharma Limited ... completed"; the predecessor's AUD/IFRS facts stay out of gold (one currency per company) |
| DVN Devon (DE) | Devon (OK) | owner's call | 1999 holding company formed for the PennzEnergy merger (a merger partner); likely the accounting predecessor; no stored words |
| COP ConocoPhillips | Phillips (CIK 78214) / Conoco | owner's call | merger of equals under a new holdco; Phillips is the accounting predecessor (lean: Phillips), Conoco not |
| RF Regions (2004) | Regions (old) | owner's call | its 10-K: "successor by merger to Union Planters Corporation and the former Regions" (a merger) |
| GPK Graphic Packaging Holding | Graphic Packaging Corp | owner's call | its 10-K: the businesses of GPC and Altivity "were combined" (a merger); GPC the predecessor |
| FMS Fresenius Medical Care | FMC AG (1996-2006) | owner's call | legal-form conversion of one company, but the old CIK filed 6-Ks after the move; nothing stored to read |
| BUD AB InBev SA/NV | Anheuser-Busch InBev S.A. | owner's call | old AB InBev merged into Newbelco (which took SAB); the predecessor's facts aren't loaded, so no figure check |
| AU AngloGold plc | AngloGold Ashanti Ltd | owner's call | documented reorganisation, but 68 of 119 equal: the successor restated its history under US GAAP (10-K), the predecessor filed IFRS (20-F): linking joins two bases |
| SBET Sharplink | SharpLink Gaming Ltd | not linked | 57 of 139; that CIK's history before 2021 is MER Telemanagement's (a reverse merger), not SharpLink's |
| LION Lionsgate Studios (2025) | Lionsgate Studios Holding Corp | not linked | 71 of 156 equal; the Starz separation changed what the company is |
| PNFP Pinnacle (2026) | Pinnacle (old) | owner's call | merger of equals with Synovus; 108 of 139 equal at its rounding (78%) but 29 of 40 core (72%): leans legacy Pinnacle |
| PNFP Pinnacle (2026) | Synovus | not linked | 61 of 160 |
| NAVI Navient Holdings | Navient LLC (company 0001593538) | owner's call | 47 of 47 equal: one company, but the predecessor is a company here, so a successions.csv change (which id survives), not this file |
| WQEY WISeQey | WISeQey (company 0001738699) | owner's call | same name, address, phone; predecessor a company here (successions.csv); the successor has no report yet |
| CDLR Cadeler plc | Cadeler A/S (company 0001978867) | owner's call | as WQEY: a successions.csv decision, no successor report yet |
| CBAT CBAK Energy Ltd | CBAK Energy Inc | owner's call | redomicile, same name and street; the successor has filed no report and nothing is stored: decide by figures after its first |

The uncertain file's other rows (not marked worth a look) were not re-checked.
