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
