# Query API test fixtures

- `tiingo_daily_meta_spy.json`, `tiingo_daily_prices_nvda.json`: Tiingo end-of-day responses
  (`/tiingo/daily/<ticker>` and `/tiingo/daily/<ticker>/prices`) in the field shape Tiingo
  documents (https://www.tiingo.com/documentation/end-of-day). They are not recorded from the
  live API (there is no key in this repository) and the prices are illustrative. Before launch,
  replace them with responses recorded once with a real key (the responses carry no key).
