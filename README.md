# montana-hottest-days-analysis
Was Sunday July 12th, 2026's heat wave historic?

## Fetch Montana daily maximum temperatures

Use NOAA's public GHCN-Daily per-station archive instead of the rate-limited
Climate Data Online API:

```bash
python3 scripts/fetch_noaa_tmax.py
```

No API token or Python packages are required. By default the script downloads
every Montana station in NOAA's current catalog that has a TMAX inventory
record, including discontinued historical stations. Downloads are restartable;
existing files are reused. Run with `--refresh` to update them.

Outputs in `weather-data/`:

- `montana_statewide_daily_tmax.csv`: one maximum for every date, chronological
- `montana_statewide_daily_tmax_ranked.csv`: the same rows, hottest first
- `station-tmax/*.csv`: readable TMAX history for every selected station
- `raw-station-archives/*.csv.gz`: NOAA's original all-element station files
- `metadata/selected_stations.csv`: the exact station universe used

The statewide output excludes observations with a nonblank NOAA GHCN quality
flag and preserves tied stations. `stations_reporting_tmax` is included so low
historical coverage is visible rather than hidden.

Useful alternatives:

```bash
# Only the supplied current-station list
python3 scripts/fetch_noaa_tmax.py --station-source all-csv

# Only the supplied airport list
python3 scripts/fetch_noaa_tmax.py --station-source airports

# Restrict summary rows (not downloads) to March through October
python3 scripts/fetch_noaa_tmax.py --months 3-10

# Small end-to-end test download
python3 scripts/fetch_noaa_tmax.py --limit 3
```

GHCN-Daily TMAX values are station-defined daily summaries. Observation-day
boundaries can vary with station observation time, especially in historical
cooperative records; the result is the highest accepted reported daily TMAX,
not an instantaneous synchronized statewide measurement.
