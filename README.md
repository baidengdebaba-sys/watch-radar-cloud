# Watch Radar Cloud Collector

Hourly GitHub Actions collector for public second-hand watch market data.

- Output: `data/latest.json`
- Credentials are not committed.
- Goofish session is stored only as GitHub Actions Secret `GOOFISH_STORAGE_STATE_B64`.
- The private Watch Radar app reads this public market-data file.
