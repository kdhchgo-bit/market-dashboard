# Market Dashboard

A Windows desktop **market regime room**. One screen collects roughly one hundred
instruments and macro series through 102 bundled Python collectors, keeps every
observation as newline-delimited JSON on local disk, and turns those series into a
single regime verdict plus the long and short setup candidates that survive it.

US equities and ETFs, crypto, commodities, the dollar, rates, volatility, option
chains, an economic calendar and news all land in the same shape, so the app can
answer one question at a time of day: *what regime are we in, and is anything
tradeable inside it*.

Every candidate has to clear five checks in order:

1. global calendar and geopolitics (BLS, FOMC, ECB freshness, release blackout, oil shock)
2. market regime (regular US session, fresh SPY bar, SPY 1h above zero, VIX9D <= VIX3M; for crypto a fresh BTC bar above zero)
3. relative strength against its sector, theme and benchmark on identical bar timestamps
4. same-time-of-day turnover multiple against the prior 20 sessions with enough samples
5. option-chain quality: snapshot age, at least three strikes, both sides ATM, positive bids, median spread inside the limit

When nothing passes, the screen names the stage that rejected the market instead of
going blank.

<!-- screenshot -->
No screenshot is committed yet. Add `docs/screenshot.png` and replace this line with
`![Market Dashboard](docs/screenshot.png)` when one exists.

## Features

- **Setup chart** with draggable entry, TP1-TP3 and SL lines plus a draggable entry
  box. Dragging recomputes price, planned average, risk width and reward/risk live;
  the entry zone moves as a block so its width is preserved, and an ordering that
  cannot work is refused with the reason printed on screen.
- **Manual edits** are kept per ticker, side and timeframe while the setup window is
  open, survive the 5-minute refresh, and can be dropped with "restore automatic
  values".
- **Long/short ranking**: the left column lists passing candidates per direction in
  rank order, the right side shows candles, entry zone, confirmed TP levels, SL and R
  multiples for the selected ticker on 5, 15 or 60-minute bars.
- **Decision card**: today's call, next evaluation time, US and crypto regime with the
  blocking reason, and a countdown to the next macro release.
- **Macro strip** (gold, crude, copper, dollar index, US 2Y, US 10Y, VIX) with the
  source observation time in the tooltip; clicking opens the full commodity, dollar
  and rate view.
- **Sector and theme charts** drawn as cumulative excess return against SPY or BTC,
  with the member list and the pass/hold reason for each line.
- **Calendar, headlines and option risk** panels (put/call ratios, VIX term structure,
  SKEW, VVIX, expiry calendar), each expandable; news titles open the original page.
- **Receive status** built from the files on disk: valid rows, original observation
  time, exclusion reason, and a searchable list of all 102 sources with the base 70 /
  option 32 counters shown on the main screen.
- **Collector status**: running, waiting, done, failed per program, including a missing
  API key. A finished process and a valid received row are reported separately.
- Keyboard: F5 collect now, Ctrl+O open the data folder, F11 fullscreen, Esc closes
  overlay, then selection, then fullscreen. Closing the window stops the collectors it
  started.

## Architecture

- GUI: WPF on .NET Framework 4.8, compiled by the in-box `csc.exe` into one
  `MarketDashboard.exe` (about 0.5 MB) with `Dashboard.xaml` embedded as a resource. No
  NuGet, no MSBuild, no installer. It is a single-window, code-behind app: no MVVM, no
  data binding, controls reached by `x:Name`.
- `collectors/collector_host.py` is the supervisor. It reads `collector_routes.json`,
  keeps at most four collectors alive, enforces a per-route timeout, republishes
  `collector-state.json` about once a second and honours `collector-control.json` for
  stop and collect-now.
- `src/DataStore.cs` reads JSONL with `FileShare.ReadWrite` and consumes only appended
  bytes, so the GUI never blocks a collector that is still writing.
- `src/Engine.cs` rebuilds the whole model (regimes, candidates, checks, charts, events,
  news, risk, status) from those files on every pass. Nothing is cached across runs.
- Storage is plain files under `collectors/data`. No database, no server, and the GUI
  itself makes no outbound request; all traffic comes from the Python collectors.

Deeper notes on the module map, threading, pipeline stages and known issues are in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Package layout:

```
MarketDashboard.exe           compiled GUI (WPF, any CPU)
MarketDashboard.exe.config    11 thresholds plus the v4.8 runtime pin
runtime/                      bundled CPython 3.13.17 embeddable + site-packages
collectors/                   102 collectors, collector_host.py, spot_history.py,
                              collector_routes.json, and data/
fonts/                        Pretendard Regular / SemiBold / Bold + OFL.txt
src/                          15 C# files and Dashboard.xaml
build.ps1                     Release build through the framework csc.exe
```

## Requirements

- Windows 10 or 11, x64.
- .NET Framework 4.8 (present in current Windows builds; the config pins `v4.8`).
- The `runtime/` folder from the packaged build, which holds the official CPython
  3.13.17 embeddable distribution with `requirements.txt` already installed. The GUI
  starts `runtime/python.exe` by path; without that folder it opens normally and shows
  the reason in the collector panel instead of crashing.
- To run collectors with your own interpreter: Python 3.13 x64 plus
  `pip install -r requirements.txt` (requests, certifi, charset-normalizer, idna,
  urllib3, tzdata).
- Outbound HTTPS. `TRADIER_TOKEN` or `TRADIER_API_KEY` in the environment is optional
  and used only by the chain collectors that try Tradier first; without it they fall
  back to keyless public endpoints.

## Quick start

Packaged build:

1. Extract the whole folder, not just the exe. `MarketDashboard.exe`,
   `MarketDashboard.exe.config`, `runtime/`, `collectors/` and `fonts/` must stay
   together; the folder can be moved as a unit afterwards.
2. Double-click `MarketDashboard.exe`. Collectors start automatically and the GUI picks
   up new rows as they are written.
3. Data always goes to `collectors/data` inside the package (Ctrl+O opens it). Runtime
   state, control files and collector logs go to `work/`.
4. One instance per folder; a second launch shows an "already running" message.

Build from source:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

`build.ps1` finds `csc.exe` under `%WINDIR%\Microsoft.NET\Framework64\v4.0.30319`
(falling back to the 32-bit path), compiles every `src/*.cs` with
`/target:winexe /langversion:5 /codepage:65001`, and writes `MarketDashboard.exe` next
to the script plus a fresh copy of the `.config`. It accepts `-OutputDirectory` and
`-ScratchDirectory`; the scratch folder defaults to `%TEMP%\market_dashboard_build`.
The source files are UTF-8 without BOM, so `/codepage:65001` must stay. The compiler
ceiling is C# 5.

## Collectors

102 numbered collectors plus two support scripts (`collector_host.py`,
`spot_history.py`). Cadence is the default route interval; each route also carries a
timeout, first-run arguments and repeat-run arguments.

| # | Theme | Sources | Cadence |
| --- | --- | --- | --- |
| 01-04 | Commodity and dollar ETFs | GLD, USO, CPER, UUP | 5 min |
| 05-10 | Index and sector ETFs | SPY, QQQ, XLK, XLE, XLF, XLV | 5 min |
| 11-18 | Single stocks: semis, energy, banks, health | NVDA, MSFT, XOM, CVX, JPM, BAC, LLY, JNJ | 5 min |
| 19-25 | Crypto majors and DeFi | BTC, ETH, SOL, AVAX, AAVE, UNI, CRV | 5 min |
| 26-30 | Rates, macro calendar, world news | US_YIELDS, BLS, FOMC, ECB, BBC_WORLD | 1 h, 6 h, 5 min |
| 31-50 | Sector, index, bond, commodity and FX ETFs | XLI, XLU, XLP, XLY, XLB, XLRE, XLC, DIA, IWM, TLT, IEF, HYG, VXX, EEM, EFA, SLV, UNG, DBA, FXE, FXY | 5 min |
| 51-60 | Single stocks: mega-cap, semis, retail, payments | AAPL, AMZN, GOOGL, META, TSLA, AMD, AVGO, WMT, UNH, MA | 5 min |
| 61-66 | Crypto alts | XRP, DOGE, ADA, LINK, ARB, OP | 5 min |
| 67-70 | Macro series, dollar index, volatility, crypto news | FRED, DXY, VIX, COINDESK | 1 h or 5 min |
| 71-75 | CBOE volatility and put/call statistics | PCR_TOTAL, PCR_EQUITY, PCR_INDEX, VIX_TERM, SKEW | 1 h |
| 76-78 | Index option chains | SPX, VIX, RUT | 15 min |
| 79-98 | ETF and single-stock option chains | SPY, QQQ, DIA, IWM, TLT, GLD, USO, XLK, XLF, XLV, EEM, SLV, NVDA, MSFT, AAPL, AMZN, GOOGL, META, TSLA, AMD | 15 min |
| 99-100 | Vol-of-vol and expiry calendar | VVIX, OCC_EXPIRY | 1 h, 6 h |
| 101-102 | Deribit option chains | BTC_DERIBIT, ETH_DERIBIT | 15 min |

Adding a source means four edits: a collector script that writes the agreed row shape,
a route object in `collector_routes.json`, a `Source` entry in `src/Catalog.cs`, and a
rebuild. Numbering above 70 makes it count as an "option" source in the status counters.

## Configuration

`MarketDashboard.exe.config` holds 11 `appSettings` thresholds. They are read once at
start, so restart after editing. A missing or out-of-range value falls back to the
compiled default in `src/Engine.cs`.

| Key | Default | Unit | Meaning |
| --- | --- | --- | --- |
| `SpreadMax` | 0.15 | fraction of mid (15%) | Largest acceptable median bid/ask spread across the ATM quotes of the front expiry |
| `BarStaleMin` | 10 | minutes | Age after which the current 5-minute SPY or BTC bar blocks that market's regime |
| `UsChainMaxAgeMin` | 35 | minutes | Maximum age of a US option-chain snapshot for the option gate |
| `CoinChainMaxAgeMin` | 25 | minutes | Same for Deribit |
| `VolLookback` | 20 | days | Prior sessions compared at the same clock time for the turnover multiple |
| `VolMinSamples` | 20 | samples | Minimum same-time samples before a multiple is trusted |
| `VolMultMin` | 1 | multiple | Turnover multiple a ticker must strictly exceed |
| `EventBeforeMin` | 30 | minutes | Pre-release blackout for both markets |
| `EventAfterMin` | 15 | minutes | Post-release blackout |
| `NewsWindowMin` | 60 | minutes | How long a flagged geopolitical headline keeps counting |
| `UsoPct` | 0.95 | quantile 0-1 | Percentile of absolute 1-hour USO moves that defines an oil shock |

Two units deserve care: `SpreadMax` and `UsoPct` are fractions and quantiles, not
percent values. Timeouts in `collector_routes.json` are seconds, while these settings
are minutes.

## Data layout

```
collectors/data/<TAG>/YYYY-MM-DD.jsonl
collectors/data/<TAG>/YYYY-MM-DD.history.jsonl   (crypto only, backfilled spot bars)
```

One JSON object per line: one bar, contract or observation, carrying its own source,
status and original observation timestamp next to `received_at_utc`. The file date is
the UTC date of the observation. Folder names are route tags and do not always match
the script number: `71_CBOE_PCR_TOTAL.py` writes to `PCR_TOTAL/`, `74_VIX_TERM.py` to
`VIX_TERM/`. The store only consumes appended bytes and never rewrites a file.

## Status and limitations

- The GUI has not been re-verified end to end and the full 102-source receive test has
  not been run. This is a working research prototype, not a validated product.
- A collector that exits successfully and a source that delivered a usable row are two
  different facts. The receive view exists because some sources routinely return zero
  rows through delay, a closed market, short history or a blocked endpoint.
- Collectors use third-party public endpoints with no agreement in place. Only Tradier
  is authenticated; everything else is keyless. Endpoints, formats and rate limits can
  change without notice, and respecting each provider's terms is the operator's
  responsibility. Two of 69 Yahoo-based collectors handle 429 with `Retry-After`; the
  rest do not.
- US session logic is a fixed 09:30-16:00 ET window with no holiday calendar. Off-hours
  staleness limits are hard-coded (4 days for US chains, 30 minutes for FX bars).
- Intraday retention is capped: 40 days for US and FX bars, 23 for coin bars, 6 and 2
  days for option chains, 3 for news.
- Levels are approximations from observed swings and OHLCV volume nodes, not forecasts.
  Reward/risk excludes fees and slippage, and no Elliott count is asserted; the wave
  note stays "unconfirmed".
- No tests, no headless mode, Windows and .NET Framework only, Korean interface.
- Not financial advice.

## License and third party

Project code is MIT, see `LICENSE`. That license does not cover the bundled fonts or
runtime. `THIRD_PARTY_NOTICES.txt` lists the CPython 3.13.17 embeddable runtime, the
pinned PyPI packages with their license files, and the chart/trade-math conventions
adapted from the author's `elliott-wave-report` repository. Pretendard is bundled
unmodified under the SIL Open Font License 1.1 (`fonts/OFL.txt`) and remains under that
license, not MIT.

`README.txt` is the original Korean user manual shipped with the package.