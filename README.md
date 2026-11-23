# Market Dashboard

A Windows desktop "market observation room": it runs ~102 Python collectors in the
background, writes every observation as JSONL to disk, and a WPF front end scores
stocks, ETFs and crypto for long/short setup candidates on finished 5-minute bars.

The goal is a single screen that answers one question in the morning: *is there a
tradeable setup today, and if not, why not* — with the rejection reasons shown instead
of hidden.

## What it does

- **Collector fleet** — 102 numbered collectors (ETFs, commodities, single stocks,
  crypto pairs, macro/yields, news, options chains, Deribit) spawned by
  `collectors/collector_host.py`, up to 4 at a time, each appending to
  `collectors/data/<SYMBOL>/YYYY-MM-DD.jsonl`.
- **Decision strip** — price, regime, turnover/volume and options gates are evaluated
  every 5 minutes; a symbol is a buy candidate only when every gate passes, otherwise
  the screen names the gate that failed.
- **Setup chart** — entry / TP1-3 / SL lines are draggable. Moving a line recomputes
  planned average, risk width and R:R live; auto values can be restored per symbol,
  side and timeframe. Levels are approximated from observed swing highs/lows plus
  volume concentration, not from a vendor signal feed.
- **Long/short ranking** — a separate setup window lists passing symbols per direction
  with their candle chart, entry zone, confirmed TP levels, SL and R:R.
- **Macro / news / options panels** — commodities, dollar, rates, 30 global headlines,
  CBOE ratios, VIX term structure, skew, expiry calendar, and FOMC/ECB/BLS events with
  a countdown.
- **Receive status** — per-source received / valid / source-time / exclusion reason, so
  "program finished" is never confused with "data arrived".

## Requirements

| Part | Requirement |
| --- | --- |
| GUI | Windows 10/11, .NET Framework 4.8 (WPF) |
| Collectors | Python 3.13 x86-64 with `requests`, `tzdata` (see `requirements.txt`) |
| Fonts | Pretendard static TTFs in `fonts\` (SIL OFL 1.1, bundled) |

API keys are **not** part of this repository. Collectors read existing environment
variables; a missing key shows up as a failure reason in the collector status view.

## Run

Packaged build: keep `MarketDashboard.exe`, `MarketDashboard.exe.config`, `runtime\`,
`collectors\` and `fonts\` in the same folder and double-click the exe. Do not run the
exe on its own.

From source:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
python -m pip install -r requirements.txt   # collectors only
```

`build.ps1` uses the in-box .NET Framework compiler (`csc.exe`) and produces
`MarketDashboard.exe` next to the script.

## Repository layout

```
src\                 C# / XAML source (WPF, single-window app)
collectors\          collector_host.py + 102 numbered collectors + routes
collectors\data\     JSONL observations (git-ignored, created at runtime)
fonts\               Pretendard static fonts + OFL.txt
build.ps1            Release build via csc.exe
app.manifest, app.ico, MarketDashboard.exe.config
README.txt           full Korean user manual shipped with the package
```

## Configuration

Eleven thresholds in `MarketDashboard.exe.config` (`appSettings`) control staleness,
spread, volume lookback, event/news windows and options-chain freshness. They are read
at startup, so restart the app after editing.

## Status and limitations

- The packaged GUI has not been re-verified, and a full 102-source receive test has not
  been run end to end (stated in the shipped manual).
- Collectors depend on public endpoints (exchange REST, CBOE, FRED, news feeds). Rate
  limits, layout changes and region blocks can break individual sources; the status
  view is the place to look.
- Levels and R:R are approximations from observed bars, fee-exclusive. Nothing here is
  financial advice.

## Third-party

See `THIRD_PARTY_NOTICES.txt` for the bundled CPython runtime, PyPI packages and
Pretendard license texts. Project code is MIT (see `LICENSE`).