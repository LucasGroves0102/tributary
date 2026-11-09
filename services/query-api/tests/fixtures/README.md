# Query API test fixtures

- `tiingo_daily_meta_spy.json`, `tiingo_daily_prices_nvda.json`: Tiingo end-of-day responses
  (`/tiingo/daily/<ticker>` and `/tiingo/daily/<ticker>/prices`) in the field shape Tiingo
  documents (https://www.tiingo.com/documentation/end-of-day). They are not recorded from the
  live API (there is no key in this repository) and the prices are illustrative. Before launch,
  replace them with responses recorded once with a real key (the responses carry no key).
- `releases/*.txt`: the parsed text (the parser's `html_to_text`, as `gold.filing_sections` serves
  it) of five real 8-K Item 2.02 press releases (EX-99.1), read from production through the Query
  API's sections route on 2026-11-08: Vertiv 2026-10-28 (0001628280-26-067707), Deckers
  2026-10-22 (0000910521-26-000028), NVIDIA 2026-08-26 (0001045810-26-000073), Funko 2026-11-05
  (0001704711-26-000051), PulteGroup 2026-10-22 (0000822416-26-000041). Public SEC filings;
  `test_releases.py` and `test_events_api.py` read them.
- `announcements/*.json`: thirteen real 8-Ks with Item 7.01 or 8.01 (Vertiv, United Therapeutics,
  Blackstone, Las Vegas Sands, TKO, BWX Technologies, National Fuel Gas), read from production
  through the Query API's filings and sections routes on 2026-11-09: their items and the first
  1,200 characters (events.HEAD_CHARS) of each 7.01, 8.01 and EX-99 section, which is what
  `announcements.read` gets. The file name says what each announces; `test_announcements.py` and
  `test_events_api.py` read them.
- `changes/*.json.gz`: four compared sections as the `/changes` route reads them: Deckers' 10-Q
  of 2026-10-29 (0001628280-26-068116) MD&A against its year-ago and prior 10-Q, Vertiv's 10-Q of
  2026-10-28 (0001628280-26-067893) MD&A against the prior 10-Q and Risk Factors against a year
  ago. Both section texts from production's sections route (2026-11-09) and `stored`, the rows
  `gold.section_changes` holds for them (the parser's diff-1 over those texts; their counts equal
  production's). gzip because the texts are 26-42 KB each; `test_changes.py` reads them.
- `model/`: the model route's inputs and the cases it is held to. `{nvda,deck,vrt}.json`:
  NVIDIA's, Deckers' and Vertiv's Query API answers and `gold.latest_reading` rows from
  production on 2026-11-09, in `Store.model_inputs`' shape. `b2-b7-cases.json`: the history
  statistics, flags, scenario choices and run-rate defaults the web app and agent-api were held to
  before the maths moved here (generated from agent-api's `model_stats.py`). `make_web_fixtures.py`
  regenerates the web app's model-run fixtures (`services/web/src/models/fixtures`) from this route;
  never hand-edit those. `test_model_maths.py` and `test_model_api.py` read them.
- `text_kpis/*.txt`: trimmed sections of production 10-Ks, 10-Qs and EX-99.1 releases (Boeing,
  Caterpillar, Emerson, Eaton, Lockheed Martin, Northrop Grumman, Vertiv), one per backlog or
  orders rule; the file name is the company, form, filing date and section. `test_text_kpis.py`
  reads them.
- `sic_codes.csv`: the 399 SIC codes the SEC assigns and their titles (2026-11-09);
  `test_sectors.py` checks every one falls in exactly one range of migration 046.
