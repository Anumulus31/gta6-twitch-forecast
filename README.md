# gta6-twitch-forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Anumulus31/gta6-twitch-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Anumulus31/gta6-twitch-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Forecasts Twitch concurrent viewers for GTA VI launch day (2026-11-19), hours 2–24, as five quantiles each. A log-ratio regression on the case's own rolling-midnight comparables (42 past launches): each hour's viewers as a ratio to the hour-1 print, with spreads from regression residuals. Practice loss 0.168 vs the 0.387 house baseline.

## Run it

```sh
python3 main.py
```

The program reads the case file from `$MAKEFUN_INPUT_DIR` (else `/input/`) and writes `/output/<id>.out` with 23 rows (hours 2–24), quantiles `[q05, q25, q50, q75, q95]`. Python 3.13, standard library only, about 0.13 s per case.

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
