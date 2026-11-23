# Architecture notes

Maintainer-facing notes for the market dashboard. The README covers what the app does;
this file covers how it is put together, what is fragile, and where to start when
something looks wrong.

## Entry point and startup

`MarketDash.App.Main` is `[STAThread]`. It creates a named mutex
(`Local/MarketDashboardUnified_<SHA-256 of the exe folder>`), so one instance is allowed
per install folder. It attaches `AppDomain.UnhandledException` and
`DispatcherUnhandledException` handlers that log to `work/errors.log`, then runs
`new Application { ShutdownMode = OnMainWindowClose }` with `DashboardWindow` as the
main window.

The XAML is a plain embedded resource, not BAML. `build.ps1` passes
`/resource:src/Dashboard.xaml,MarketDash.Dashboard.xaml`; the window reads that stream
and calls `XamlReader.Load`. `AppFont` is registered in `Application.Current.Resources`
before loading because the XAML references it as `{DynamicResource AppFont}`.

`Loaded` then sets fullscreen, starts the 1-second timer, starts the collectors and runs
the first refresh.

## Module map

| File | Responsibility |
| --- | --- |
| `App.cs` | `Main`, single-instance mutex, error logging, application bootstrap, settings |
| `DashboardWindow.cs` | Window: XAML load, 1 s tick loop, refresh orchestration, rendering, overlays, keys |
| `Engine.cs` | Model types, thresholds, scoring and verdict, regimes, funnel, option check, events, news, risk, status |
| `DataStore.cs` | Incremental JSONL reader: per-file offsets, tail parsing, normalization, retention, dedupe |
| `Catalog.cs` | 102-source catalog, sector and theme maps, chain fallback rules |
| `CollectorService.cs` | Spawns `python.exe collector_host.py`, Job Object, control and state files, log capture |
| `Json.cs` | Minimal JSON parser plus number, string and time accessors |
| `SetupBuilder.cs` | Builds a long or short plan: aggregation, ATR, pivots, volume nodes, entry, stop, targets, R, manual edits |
| `SetupWindow.cs` | Setup window: candidate list, direction filter, 5/15/60-minute buttons, level cards |
| `SetupChart.cs` | Native candlestick chart with draggable entry, TP and SL levels |
| `RelativeChart.cs` | Native multi-line relative-performance chart with hover labels and selection |
| `TradeSetup.cs` | DTOs for the trade plan and level edits |
| `CollectorViews.cs` | Collector status overlay table with search |
| `DetailViews.cs` | Detail panels: checks, status, risk, events, news |
| `AppFont.cs` | Font family from the bundled `fonts/` folder |
| `Dashboard.xaml` | Layout of the single window |

## Data flow

```
collector_host.py --(spawns)--> NN_*.py --(appends)--> collectors/data/<TAG>/<date>.jsonl
        ^                                                     |
        | control/state json                                  v
CollectorService  <--- collector-state.json ---  DataStore (incremental read)
                                                          |
                                                          v
                                        Engine.Run(now) -> Model -> DashboardWindow
```

- The host reads `collector_routes.json` (102 routes) and starts first runs before
  repeats, at most four processes at a time, polling every 0.25 s and republishing
  state every 1 s by temp file plus `os.replace`.
- Collector stdout is not a protocol. The host keeps the last 32 KB per run, matches a
  short keyword list (missing key, http error, rate limited, market closed, completed
  historical spot bars) and appends the full stream to `collectors/NN.log`, truncated
  at 256 KB. Exit code 0 with zero usable rows is a normal outcome.
- `DataStore` keeps per-file state (length, mtime, first 256 bytes) and re-reads only
  appended bytes. It ignores a trailing partial line and rebuilds only complete bars.
  Retention: 40 days US and FX bars, 23 coin bars, 6 and 2 days option chains, 3 days
  news, 400 daily and calendar rows.
- `Engine.Run` is pure: it takes the store and the current UTC time and returns a new
  model. The next run is the next 5-minute UTC boundary.

## Threading

All WPF work happens on the UI thread. One `DispatcherTimer` (1 s) drives the clock
label, the collector state poll, a refresh when the state's `changed` counter moved and
at least 5 s passed, the 5 s overlay rebuild, the event countdown and
`RefreshData` at `NextUtc`. `RefreshData` is `async void`, guards re-entry with
`busy`/`pending`, and moves store refresh plus scoring into `Task.Run`, so scoring never
blocks the UI. Collector output arrives on pool threads through `BeginOutputReadLine`
and is written under a lock.

## Decision pipeline

1. Status, top band, events, news and risk are assembled first and feed later gates.
2. Calendar and geopolitics: BLS, FOMC and ECB must be fresh and carry a future event,
   otherwise the market is blocked. A release inside -30/+15 minutes blocks both
   markets. A BBC headline matching the geopolitical keyword list inside the news
   window, combined with a USO 1-hour move above its percentile, blocks as well.
3. Regime: US needs a regular session, a SPY 5-minute bar no older than the staleness
   limit, SPY 1h above zero and VIX9D at or below VIX3M on the same ET observation date.
   Crypto needs a fresh BTC 5-minute bar and BTC 1h above zero.
4. Relative strength: ticker above group above benchmark above zero, on 1-hour returns
   at identical bar timestamps. Shorts run the same funnel inverted.
5. Turnover: current 5-minute notional divided by the median notional at the same ET
   clock time over the prior sessions, requiring enough samples and a multiple above
   the threshold.
6. Options: snapshot age, nearest expiry only, at least three strikes, ATM +/-1 with
   both call and put, positive bids and bids not above asks, median spread inside the
   limit. ATM implied volatility is displayed but is not a gate.
7. Verdict: candidates that pass every stage are sorted by turnover multiple and excess
   return. If nothing passes, the best stage-4 or stage-3 candidate is shown as a watch
   item together with its blocking reason.

## Process and security notes

- Collectors are launched list-form through `subprocess.Popen` without a shell. Route
  arguments come from a static JSON file, and `TEMP` is redirected into `work/`. No user
  input reaches a command line or a file path.
- The C# side builds one argument string with naive quote wrapping for the host process
  path. Only an install folder containing a double quote would break it.
- Child processes are tied to a Job Object with kill-on-close, and the host also watches
  the parent PID, so closing the GUI takes the collectors down twice over.
- News links reach `Process.Start` only after an http or https scheme check.

## Known issues

- `DataStore.ParseRow` has no per-line guard while the file offset has already advanced,
  so one throwing row silently drops the remainder of that file until it rotates.
- `SetupWindow` reads bar lists on the UI thread while a background refresh may replace
  them. Lists are swapped wholesale, so the realistic symptom is stale data rather than
  a crash, but it is unsynchronized.
- There is no runtime cross-check between `Catalog.All` and `collector_routes.json`; a
  tag present in one and missing from the other shows up only as a missing folder.
- `Catalog` duplicates symbol lists that also appear in `RelativeChart`, the chip lists
  in `DashboardWindow` and `Engine`, and `collector_routes.json`. Adding an instrument
  means touching all of them.
- `Cfg` values are `static readonly` and read at first use, so threshold edits require
  an app restart.
- `/langversion:5` is a real ceiling: the framework compiler cannot do more, so newer C#
  syntax will not build.
- No unit tests and no headless mode; the only verification path is running the GUI.

## Adding collector 103

1. Write `103_<NAME>.py` that accepts the route arguments and appends rows to
   `data/<TAG>/<date>.jsonl` with `source`, an observed-time key, `received_at_utc` and
   `status`.
2. Append a route object (`no`, `file`, `tag`, `kind`, `interval`, `timeout`,
   `args_first`, `args_repeat`) to `collector_routes.json`.
3. Add the source to `src/Catalog.cs`, otherwise it renders nowhere. Numbers above 70
   count as option sources in the 70/32 counters.
4. Rebuild with `build.ps1`. Dependencies stay limited to `requests` and `tzdata`.