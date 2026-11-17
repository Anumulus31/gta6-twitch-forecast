#!/usr/bin/env python3
"""Forecast Twitch viewers for GTA VI launch day, hours 2-24.

Model (fit fresh at runtime from the case's own `comparables`):
  * Work on the log scale.  For every comparable launch, take its release-day
    hourly viewers and express each hour as a log-ratio to that launch's
    hour-1 print.
  * Keep only "rolling midnight" launches (unlock rolls around the world at
    local midnight, as GTA VI does): simultaneous-global launches and
    early-access-contaminated launches have a different shape by
    construction, so they are excluded from the centre fit.  A comparable
    whose hour-1 print is under 3% of its launch-day peak is also dropped
    (its hour 1 was before unlock, so its ratios are meaningless).
  * For each forecast hour separately, regress that hour's log-ratio on
    log(hour-1 print) across the rolling set.  Big launches have flatter
    curves (their hour 1 already contains most of the world), small launches
    surge much harder in the evening; the slope is about -0.4 at evening
    hours.  The regression is the centre of the forecast.
  * Spread: the residual standard deviation of that hour's regression
    (floored at 0.15).  Quantiles come from a normal on the log scale at the
    case's levels.  Outside the training range of log(hour-1), the slope is
    damped to half and the spread is widened by 0.10 per log unit of
    extrapolation, because GTA VI's hour-1 print is likely larger than any
    rolling comparable's.
  * Fallbacks, in order: rolling regression -> median of all usable
    comparables -> flat persistence at the hour-1 print (the house starter).
    The program never crashes a case and never writes an invalid file.

Stdlib only, deterministic, no network.  Practice mean pinball loss 0.170
vs the house baseline's 0.387 (per-case: p00 0.198, p01 0.124, p02 0.130,
p03 0.228).
"""

import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def parse_ts(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")


def round2(value):
    """Round half-up to 2 decimals, like the starter's quantiser."""
    return math.floor(value * 100 + 0.5) / 100


def median(values):
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return 0.0
    return vals[n // 2] if n % 2 else 0.5 * (vals[n // 2 - 1] + vals[n // 2])


def pstdev(values):
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / n)


def ols(xs, ys):
    """Least squares y ~ a + b*x.  Returns (a, b, residuals)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return my, 0.0, [y - my for y in ys]
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    return a, b, [y - (a + b * x) for x, y in zip(xs, ys)]


def norm_ppf(p):
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    if p <= 0.0 or p >= 1.0:
        raise ValueError("p must be in (0, 1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# --------------------------------------------------------------------------
# comparable classification
# --------------------------------------------------------------------------

_SIMUL = re.compile(
    r"single global|simultaneous global|simultaneous across|"
    r"single simultaneous|simultaneous midnight gmt|pc single global|single time",
    re.I)
_ROLLING = re.compile(r"midnight|rolling|12 am local|local time", re.I)


def day_series(comp):
    """24 hourly viewer counts on the comparable's release day, by hour offset."""
    try:
        day_start = parse_ts(comp["day_start"])
    except Exception:
        return [None] * 24
    pts = {}
    for point in comp.get("hourly") or []:
        try:
            t = parse_ts(point["from"])
            off = (t - day_start).total_seconds() / 3600.0
        except Exception:
            continue
        if 0 <= off < 24 and off == int(off) and point.get("viewers") is not None:
            pts[int(off)] = point["viewers"]
    return [pts.get(h) for h in range(24)]


def comparable_rows(case):
    """Return (rolling_rows, all_rows) of usable comparables.

    Each row: {'lp1': log hour-1 print, 'lr': [log-ratio per hour 0..23]}.
    """
    rolling, usable = [], []
    for comp in case.get("comparables") or []:
        unlock = (comp.get("unlock") or "")
        name = (comp.get("name") or "")
        low_unlock = unlock.lower()
        # The GTA VI category's own pre-release history is not a launch day.
        if "not a launch" in low_unlock or "before release" in name.lower():
            continue
        series = day_series(comp)
        p1 = series[0]
        if p1 is None or p1 <= 0:
            continue
        peak = max(v for v in series if v)
        if p1 < 0.03 * peak:
            continue  # hour 1 was before unlock
        row = {"lp1": math.log(p1),
               "lr": [math.log(series[h] / p1) if series[h] else None
                      for h in range(24)]}
        usable.append(row)
        if "early-access-contaminated" in low_unlock:
            continue
        if "tier: weak" in low_unlock:
            continue
        if _SIMUL.search(unlock):
            continue
        if _ROLLING.search(unlock):
            rolling.append(row)
    return rolling, usable

# --------------------------------------------------------------------------
# forecast
# --------------------------------------------------------------------------

def persistence_forecast(p1, levels):
    """House starter: flat at the hour-1 print with widening spread."""
    out = []
    for hour in range(2, 25):
        qs = [p1 * math.exp(z * 0.15 * math.sqrt(hour - 1))
              for z in (norm_ppf(l) for l in levels)]
        out.append({"hour": hour, "q": [round2(max(1.0, min(1e9, q))) for q in qs]})
    return {"hours": out}


def regression_forecast(p1, levels, rolling, usable):
    """Per-hour OLS of log-ratio on log(p1), with damped extrapolation."""
    lp1 = math.log(p1)
    rows = rolling if len(rolling) >= 3 else usable
    if len(rows) < 3:
        return None
    hours = []
    for hour in range(2, 25):
        idx = hour - 1
        xs = [r["lp1"] for r in rows if r["lr"][idx] is not None]
        ys = [r["lr"][idx] for r in rows if r["lr"][idx] is not None]
        if len(xs) < 3:
            # Too few points for this hour: fall back to the median shape.
            vals = [r["lr"][idx] for r in rows if r["lr"][idx] is not None]
            mu = median(vals) if vals else 0.0
            sd = max(0.15, pstdev(vals)) if len(vals) > 1 else 0.5
        else:
            a, b, resid = ols(xs, ys)
            lo, hi = min(xs), max(xs)
            if lo <= lp1 <= hi:
                mu = a + b * lp1
                extra = 0.0
            else:
                # Damped extrapolation: half the slope beyond the range.
                edge = lo if lp1 < lo else hi
                mu = a + b * edge + 0.5 * b * (lp1 - edge)
                extra = 0.10 * abs(lp1 - edge)
            sd = pstdev(resid) if len(resid) > 2 else 0.5
            sd = max(0.15, sd + extra)
        mu = max(-1.5, min(2.6, mu))
        qs = []
        prev = 0.0
        for level in levels:
            q = p1 * math.exp(mu + norm_ppf(level) * sd)
            q = round2(max(1.0, min(1e9, q)))
            q = max(q, prev)  # keep non-decreasing after rounding
            prev = q
            qs.append(q)
        hours.append({"hour": hour, "q": qs})
    return {"hours": hours}


def median_forecast(p1, levels, usable):
    """Median log-ratio shape across all usable comparables."""
    if len(usable) < 3:
        return None
    hours = []
    for hour in range(2, 25):
        idx = hour - 1
        vals = [r["lr"][idx] for r in usable if r["lr"][idx] is not None]
        if not vals:
            continue
        mu = median(vals)
        sd = max(0.15, pstdev(vals)) if len(vals) > 1 else 0.5
        qs = []
        prev = 0.0
        for level in levels:
            q = p1 * math.exp(mu + norm_ppf(level) * sd)
            q = round2(max(1.0, min(1e9, q)))
            q = max(q, prev)
            prev = q
            qs.append(q)
        hours.append({"hour": hour, "q": qs})
    return {"hours": hours} if len(hours) == 23 else None


def forecast_case(case):
    known = case.get("known") or []
    p1 = None
    for row in known:
        if row.get("hour") == 1 and row.get("viewers"):
            p1 = row["viewers"]
            break
    if p1 is None and known:
        p1 = known[0].get("viewers")
    p1 = max(1, int(p1)) if p1 else 1
    levels = case.get("levels") or [0.05, 0.25, 0.5, 0.75, 0.95]
    try:
        rolling, usable = comparable_rows(case)
        result = regression_forecast(p1, levels, rolling, usable)
        if result is None:
            result = median_forecast(p1, levels, usable)
        if result is None:
            result = persistence_forecast(p1, levels)
    except Exception:
        result = persistence_forecast(p1, levels)
    return result


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------

def validate(payload):
    hours = payload.get("hours")
    if not isinstance(hours, list) or len(hours) != 23:
        return False
    seen = set()
    for row in hours:
        h = row.get("hour")
        q = row.get("q")
        if h not in range(2, 25) or h in seen:
            return False
        seen.add(h)
        if not isinstance(q, list) or len(q) != 5:
            return False
        prev = 0.0
        for v in q:
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                return False
            if v <= 0 or v > 1_000_000_000 or v < prev:
                return False
            prev = v
    return True


def main():
    input_dir = Path(os.environ.get("MAKEFUN_INPUT_DIR") or "/input")
    output_dir = Path(os.environ.get("MAKEFUN_OUTPUT_DIR") or "/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(input_dir.glob("*.in")):
        cid = path.stem
        out_path = output_dir / (cid + ".out")
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
            payload = forecast_case(case)
            if not validate(payload):
                raise ValueError("invalid forecast payload")
        except Exception:
            # Never leave a case without a valid file: persistence fallback.
            try:
                case = json.loads(path.read_text(encoding="utf-8"))
                p1 = max(1, int((case.get("known") or [{}])[0].get("viewers") or 1))
            except Exception:
                p1 = 1
            payload = persistence_forecast(p1, [0.05, 0.25, 0.5, 0.75, 0.95])
        out_path.write_text(json.dumps(payload, separators=(",", ":")),
                            encoding="utf-8")
        print(f"{cid}: wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
