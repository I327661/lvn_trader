"""
LVN Alert & Mock Trade Tracker — CRUDEOIL NOV FUT
===================================================
Reads  levels/lvn_levels.json  for today's D-2 / D-1 LVN zones,
polls  Fyers API  every 30 s for live CRUDEOIL LTP,
auto-enters mock trades when price taps a zone,
tracks SL / target on both futures and ITM options,
appends results to  trades.csv.

Tap logic
  Zone = [upper_line, lower_line]
  CE  price falls INTO zone from above  →  bullish bounce  SL = lower_line
  PE  price rises INTO zone from below  →  bearish fade    SL = upper_line

Option selection (ITM, 50-pt intervals)
  CE  →  highest multiple of 50 below entry_ltp  (e.g. 9562 → 9550 CE)
  PE  →  lowest  multiple of 50 above entry_ltp  (e.g. 9562 → 9600 PE)

Usage
    python alert.py
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from fyers_apiv3 import fyersModel

# ── paths ─────────────────────────────────────────────────────────────────────
DIR         = Path(__file__).resolve().parent
LEVELS_FILE = DIR / "levels" / "lvn_levels.json"
TRADES_FILE = DIR / "trades.csv"
FYERS_ENV   = DIR.parent / "nifty_options_algo" / "config" / ".env"

FUT_SYMBOL   = "MCX:CRUDEOIL26NOVFUT"
OPT_PREFIX   = "MCX:CRUDEOIL26NOV"      # + "9500CE" etc.
STRIKE_STEP  = 50

IST       = timezone(timedelta(hours=5, minutes=30))
MCX_OPEN  = dtime(9,  0)
MCX_CLOSE = dtime(23, 30)


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


# ── Fyers ─────────────────────────────────────────────────────────────────────
def load_fyers(token: str | None = None, app_id: str | None = None) -> fyersModel.FyersModel:
    if token and app_id:
        return fyersModel.FyersModel(client_id=app_id, token=token, log_path="")
    if not FYERS_ENV.exists():
        sys.exit(f"[ERROR] Fyers .env not found at {FYERS_ENV}")
    load_dotenv(FYERS_ENV)
    app_id = os.getenv("BROKER__APP_ID")
    token  = os.getenv("BROKER__ACCESS_TOKEN")
    if not app_id or not token:
        sys.exit("[ERROR] BROKER__APP_ID or BROKER__ACCESS_TOKEN missing in .env")
    return fyersModel.FyersModel(client_id=app_id, token=token, log_path="")


def _fyers_ltp(fyers: fyersModel.FyersModel, symbols: list[str]) -> dict[str, float]:
    """Fetch LTP for one or more Fyers symbols. Returns {symbol: ltp}."""
    if not symbols:
        return {}
    try:
        r = fyers.quotes({"symbols": ",".join(symbols)})
        if r.get("s") != "ok":
            print(f"  [WARN] Fyers quotes error: {r.get('message','')}")
            return {}
        result = {}
        for item in r.get("d", []):
            sym = item.get("n", "")
            lp  = item.get("v", {}).get("lp")
            if sym and lp is not None:
                result[sym] = float(lp)
        return result
    except Exception as exc:
        print(f"  [WARN] Fyers fetch failed: {exc}")
        return {}


async def fetch_prices(fyers: fyersModel.FyersModel, symbols: list[str]) -> dict[str, float]:
    """Run synchronous Fyers call off the event loop thread."""
    return await asyncio.to_thread(_fyers_ltp, fyers, symbols)


# ── option symbol ──────────────────────────────────────────────────────────────
def itm_symbol(ltp: float, direction: str) -> tuple[float, str]:
    """Return (strike, fyers_symbol) for nearest ITM option."""
    if direction == "CE":
        strike = int(ltp // STRIKE_STEP) * STRIKE_STEP
    else:
        strike = (int(ltp // STRIKE_STEP) + 1) * STRIKE_STEP
    return float(strike), f"{OPT_PREFIX}{strike}{direction}"


# ── config ────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    if not LEVELS_FILE.exists():
        sys.exit(f"[ERROR] {LEVELS_FILE} not found")
    cfg = json.loads(LEVELS_FILE.read_text())
    zones: list[dict] = []
    for session in ("d2", "d1"):
        color = "RED" if session == "d2" else "GOLD"
        date_label = cfg.get(session, {}).get("date", "")
        for pair in cfg.get(session, {}).get("zones", []):
            upper, lower = float(pair[0]), float(pair[1])
            zones.append({
                "upper":   upper,
                "lower":   lower,
                "mid":     round((upper + lower) / 2, 2),
                "session": session.upper(),
                "color":   color,
                "date":    date_label,
            })
    zones.sort(key=lambda z: z["mid"])
    cfg["_zones"] = zones
    return cfg


# ── CSV ───────────────────────────────────────────────────────────────────────
CSV_HEADERS = [
    "date", "entry_time", "zone_upper", "zone_lower", "session", "direction",
    "entry_price", "sl", "target", "fut_pnl_pts",
    "option_strike", "option_type", "option_entry", "option_exit", "option_pnl",
    "exit_time", "exit_reason",
]


def ensure_csv() -> None:
    if not TRADES_FILE.exists():
        with open(TRADES_FILE, "w", newline="") as f:
            csv.writer(f).writerow(CSV_HEADERS)


def append_trade(row: dict) -> None:
    with open(TRADES_FILE, "a", newline="") as f:
        csv.writer(f).writerow([row.get(h, "") for h in CSV_HEADERS])


# ── trade ─────────────────────────────────────────────────────────────────────
class Trade:
    def __init__(self, zone, direction, entry_price, sl, target, entry_time,
                 option_strike, option_sym, option_entry):
        self.upper         = zone["upper"]
        self.lower         = zone["lower"]
        self.session       = zone["session"]
        self.color         = zone["color"]
        self.direction     = direction
        self.entry_price   = entry_price
        self.sl            = sl
        self.target        = target
        self.entry_time    = entry_time
        self.option_strike = option_strike
        self.option_sym    = option_sym
        self.option_entry  = option_entry

    def check_exit(self, ltp: float) -> str | None:
        if self.direction == "CE":
            if ltp <= self.sl:     return "SL_HIT"
            if ltp >= self.target: return "TARGET_HIT"
        else:
            if ltp >= self.sl:     return "SL_HIT"
            if ltp <= self.target: return "TARGET_HIT"
        return None

    def to_row(self, exit_price, exit_reason, exit_time, option_exit) -> dict:
        fut_pnl = round(exit_price - self.entry_price, 2) if self.direction == "CE" \
                  else round(self.entry_price - exit_price, 2)
        opt_pnl = round(option_exit - self.option_entry, 2) \
                  if self.option_entry and option_exit else ""
        return {
            "date":          self.entry_time.strftime("%Y-%m-%d"),
            "entry_time":    self.entry_time.strftime("%H:%M:%S"),
            "zone_upper":    self.upper,
            "zone_lower":    self.lower,
            "session":       self.session,
            "direction":     self.direction,
            "entry_price":   self.entry_price,
            "sl":            self.sl,
            "target":        self.target,
            "fut_pnl_pts":   fut_pnl,
            "option_strike": self.option_strike or "",
            "option_type":   self.direction,
            "option_entry":  self.option_entry or "",
            "option_exit":   option_exit or "",
            "option_pnl":    opt_pnl,
            "exit_time":     exit_time.strftime("%H:%M:%S"),
            "exit_reason":   exit_reason,
        }


def next_target(zones, tapped_zone, direction) -> float | None:
    if direction == "CE":
        above = [z for z in zones if z["lower"] > tapped_zone["upper"] + 5]
        return above[0]["lower"] if above else None
    below = [z for z in zones if z["upper"] < tapped_zone["lower"] - 5]
    return below[-1]["upper"] if below else None


# ── main ──────────────────────────────────────────────────────────────────────
async def run(state: dict | None = None) -> None:
    cfg           = load_config()
    fyers         = load_fyers(
        token=state["token"] if state else None,
        app_id=state["app_id"] if state else None,
    )
    zones         = cfg["_zones"]
    cooldown_s    = int(cfg.get("cooldown_min", 30)) * 60
    bounce_buffer = float(cfg.get("bounce_buffer", 5))
    max_sl_pts    = float(cfg.get("max_sl_pts", 30))
    reversal_pts  = float(cfg.get("reversal_pts", 20))
    poll_s        = 30

    ensure_csv()

    print(f"\nLVN Mock Trader  —  {FUT_SYMBOL}  (Fyers)")
    print(f"Zones ({len(zones)}):")
    for z in sorted(zones, key=lambda x: -x["mid"]):
        print(f"  {z['color']:4s} {z['session']}  {z['upper']:.2f} / {z['lower']:.2f}  ({z['date']})")
    print(f"\nCooldown: {cooldown_s//60} min  |  Poll: {poll_s}s  |  Trades: {TRADES_FILE}\n")

    active: Trade | None     = None
    tapped: dict[int, datetime] = {}
    zone_state: dict[int, str]  = {}
    zone_extreme: dict[int, float] = {}   # high for PE, low for CE
    prev_ltp: float | None      = None

    # pre-seed any force_tap entries from config
    for ft in cfg.get("force_tap", []):
        fu, fl, fd = float(ft[0]), float(ft[1]), ft[2].upper()
        for i, z in enumerate(zones):
            if z["upper"] == fu and z["lower"] == fl:
                zone_state[i] = f"tapped_{fd.lower()}"
                print(f"  [FORCE] zone {fu:.0f}/{fl:.0f} pre-seeded as tapped_{fd.lower()}")

    while True:
        t = now_ist()

        # ── EOD flat ──────────────────────────────────────────────────────────
        if active and t.time() >= MCX_CLOSE:
            syms = [FUT_SYMBOL] + ([active.option_sym] if active.option_sym else [])
            prices   = await fetch_prices(fyers, syms)
            ltp      = prices.get(FUT_SYMBOL, active.entry_price)
            opt_exit = prices.get(active.option_sym) if active.option_sym else None
            row = active.to_row(ltp, "EOD_FLAT", t, opt_exit)
            print(f"\n[{t:%H:%M}] EOD FLAT {active.direction} fut {ltp:.0f}  "
                  f"fut_pnl {row['fut_pnl_pts']:+.0f}  opt_pnl {row['option_pnl'] or '-'}")
            append_trade(row)
            active = None

        if t.time() < MCX_OPEN or t.time() > MCX_CLOSE:
            print(f"[{t:%H:%M}] MCX closed — waiting")
            await asyncio.sleep(60)
            continue

        # ── fetch prices ──────────────────────────────────────────────────────
        syms    = [FUT_SYMBOL] + ([active.option_sym] if active and active.option_sym else [])
        prices  = await fetch_prices(fyers, syms)
        ltp     = prices.get(FUT_SYMBOL)
        if ltp is None:
            print(f"[{t:%H:%M:%S}]  [WARN] no LTP received")
            await asyncio.sleep(poll_s)
            continue
        opt_ltp = prices.get(active.option_sym) if active and active.option_sym else None

        # ── status line ───────────────────────────────────────────────────────
        if active:
            icon    = "▲" if active.direction == "CE" else "▼"
            opt_str = f"  opt {opt_ltp:.0f}" if opt_ltp else ""
            line = (f"[{t:%H:%M:%S}]  fut {ltp:8.2f}{opt_str}  "
                    f"{icon}{active.direction} SL {active.sl:.2f}  T {active.target:.2f}")
            if state is not None:
                state["price"] = ltp
                state["trade"] = {"direction": active.direction, "entry": active.entry_price,
                                  "sl": active.sl, "target": active.target}
        else:
            nearest = min(zones, key=lambda z: min(abs(ltp - z["upper"]), abs(ltp - z["lower"])))
            dist    = ltp - nearest["mid"]
            line = (f"[{t:%H:%M:%S}]  fut {ltp:8.2f}  "
                    f"nearest {nearest['color']} {nearest['upper']:.0f}/{nearest['lower']:.0f} ({dist:+.0f})")
            if state is not None:
                state["price"] = ltp
                state["trade"] = None
        print(line)
        if state is not None:
            state["log"].append(line)
            if len(state["log"]) > 50:
                state["log"].pop(0)

        # ── reload fyers if token updated ─────────────────────────────────────
        if state is not None and state.get("token_updated"):
            fyers = load_fyers(token=state["token"], app_id=state["app_id"])
            state["token_updated"] = False
            print("  [INFO] Fyers token reloaded")

        # ── reload zones if levels updated ────────────────────────────────────
        if state is not None and state.get("levels_updated"):
            cfg = load_config()
            zones = cfg["_zones"]
            cooldown_s    = int(cfg.get("cooldown_min", 30)) * 60
            bounce_buffer = float(cfg.get("bounce_buffer", 5))
            max_sl_pts    = float(cfg.get("max_sl_pts", 30))
            reversal_pts  = float(cfg.get("reversal_pts", 20))
            zone_state.clear()
            zone_extreme.clear()
            state["levels_updated"] = False
            print(f"  [INFO] Zones reloaded ({len(zones)} zones)")

        # ── check exit ────────────────────────────────────────────────────────
        if active:
            reason = active.check_exit(ltp)
            if reason:
                row  = active.to_row(ltp, reason, t, opt_ltp)
                mark = "✓" if float(row["fut_pnl_pts"]) > 0 else "✗"
                print(f"\n  {mark} {reason}  {active.color} {active.session} {active.direction}  "
                      f"entry {active.entry_price:.2f} → {ltp:.2f}  "
                      f"fut {row['fut_pnl_pts']:+}pts  opt {row['option_pnl'] or '-'}\n")
                append_trade(row)
                active = None

        # ── zone tap / reaction ───────────────────────────────────────────────
        if active is None and prev_ltp is not None:
            for i, zone in enumerate(zones):
                upper, lower = zone["upper"], zone["lower"]

                last = tapped.get(i)
                if last and (t - last).total_seconds() < cooldown_s:
                    continue

                st = zone_state.get(i, "idle")

                if st == "idle":
                    in_zone = lower <= ltp      <= upper
                    was_out = not (lower <= prev_ltp <= upper)
                    if in_zone and was_out:
                        if prev_ltp > upper:
                            zone_state[i] = "tapped_ce"
                            zone_extreme[i] = ltp          # track low for CE
                            print(f"  ◎ TAPPED CE  {zone['color']} {zone['session']}  "
                                  f"{upper:.0f}/{lower:.0f} — waiting for bounce above {upper + bounce_buffer:.2f}")
                        elif prev_ltp < lower:
                            zone_state[i] = "tapped_pe"
                            zone_extreme[i] = ltp          # track high for PE
                            print(f"  ◎ TAPPED PE  {zone['color']} {zone['session']}  "
                                  f"{upper:.0f}/{lower:.0f} — waiting for fade below {lower - bounce_buffer:.2f}")
                    continue

                entry_model = None

                if st == "tapped_ce":
                    zone_extreme[i] = min(zone_extreme.get(i, ltp), ltp)   # track lowest inside
                    if ltp >= upper + bounce_buffer:
                        direction   = "CE"
                        entry_model = "boundary"
                    elif ltp >= zone_extreme[i] + reversal_pts:
                        direction   = "CE"
                        entry_model = "reversal"
                    elif ltp < lower:
                        zone_state[i]   = "idle"
                        zone_extreme.pop(i, None)
                        print(f"  ✗ ZONE FAILED  {zone['session']} {upper:.0f}/{lower:.0f} — broke below")
                        continue
                    else:
                        continue

                elif st == "tapped_pe":
                    zone_extreme[i] = max(zone_extreme.get(i, ltp), ltp)   # track highest inside
                    if ltp < lower - bounce_buffer:
                        direction   = "PE"
                        entry_model = "boundary"
                    elif ltp <= zone_extreme[i] - reversal_pts:
                        direction   = "PE"
                        entry_model = "reversal"
                    elif ltp > upper:
                        zone_state[i]   = "idle"
                        zone_extreme.pop(i, None)
                        print(f"  ✗ ZONE FAILED  {zone['session']} {upper:.0f}/{lower:.0f} — broke above")
                        continue
                    else:
                        continue
                else:
                    continue

                # confirmed → compute SL from zone extreme (lowest inside for CE, highest for PE)
                if direction == "CE":
                    sl   = zone_extreme[i] - bounce_buffer
                    risk = ltp - sl
                else:
                    sl   = zone_extreme[i] + bounce_buffer
                    risk = sl - ltp

                # skip if risk exceeds max_sl_pts
                if risk > max_sl_pts:
                    zone_state[i] = "idle"
                    zone_extreme.pop(i, None)
                    tapped[i] = t
                    print(f"  [SKIP] {zone['session']} {upper:.0f}/{lower:.0f} {direction} "
                          f"— risk {risk:.0f}pts > {max_sl_pts:.0f}pts max")
                    continue
                target = next_target(zones, zone, direction)
                tapped[i]     = t
                zone_state[i] = "idle"
                zone_extreme.pop(i, None)

                if target is None:
                    print(f"  [SKIP] {zone['session']} {upper:.0f}/{lower:.0f} {direction} — no target")
                    continue

                opt_strike, opt_sym, opt_premium = None, None, None
                opt_strike, opt_sym = itm_symbol(ltp, direction)
                opt_prices  = await fetch_prices(fyers, [opt_sym])
                opt_premium = opt_prices.get(opt_sym)

                active = Trade(zone, direction, ltp, sl, target, t,
                               opt_strike, opt_sym, opt_premium)

                opt_str = (f"  |  {direction} {opt_strike:.0f} @ {opt_premium:.0f}"
                           if opt_premium else "  |  option N/A")
                print(f"\n  ★ ENTER [{entry_model}]  {zone['color']} {zone['session']}  "
                      f"{upper:.2f}/{lower:.2f}  ({zone['date']})\n"
                      f"    {direction}  fut {ltp:.2f}  SL {sl:.2f}  T {target:.2f}{opt_str}\n")
                break

        prev_ltp = ltp
        await asyncio.sleep(poll_s)


if __name__ == "__main__":
    asyncio.run(run())
