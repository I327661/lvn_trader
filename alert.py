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
TRADES_DIR  = DIR / "trades"
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
        for entry in cfg.get(session, {}).get("zones", []):
            if isinstance(entry, list):
                upper, lower = float(entry[0]), float(entry[1])
                zone_id   = f"{upper:.0f}_{lower:.0f}"
                zone_type = "neutral"
            else:
                upper     = float(entry["upper"])
                lower     = float(entry["lower"])
                zone_id   = str(entry["id"])
                zone_type = entry.get("type", "neutral")
            zones.append({
                "id":      zone_id,
                "upper":   upper,
                "lower":   lower,
                "mid":     round((upper + lower) / 2, 2),
                "type":    zone_type,
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
    "exit_time", "exit_reason", "lot",
]


def daily_csv() -> Path:
    return TRADES_DIR / f"trades_{now_ist().strftime('%Y-%m-%d')}.csv"


def ensure_csv() -> None:
    TRADES_DIR.mkdir(exist_ok=True)
    path = daily_csv()
    if not path.exists():
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(CSV_HEADERS)


def append_trade(row: dict, state: dict | None = None) -> None:
    path = daily_csv()
    if not path.exists():
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(CSV_HEADERS)
    with open(path, "a", newline="") as f:
        csv.writer(f).writerow([row.get(h, "") for h in CSV_HEADERS])
    if state is not None:
        state["trades"].append({
            "exit_time":   row.get("exit_time", ""),
            "direction":   row.get("direction", ""),
            "zone_upper":  row.get("zone_upper", ""),
            "zone_lower":  row.get("zone_lower", ""),
            "exit_reason": row.get("exit_reason", ""),
            "fut_pnl_pts": row.get("fut_pnl_pts", ""),
            "lot":         row.get("lot", ""),
        })


# ── trade ─────────────────────────────────────────────────────────────────────
class Trade:
    def __init__(self, zone, zone_id, direction, entry_price, sl, target, entry_time,
                 option_strike, option_sym, option_entry):
        self.upper         = zone["upper"]
        self.lower         = zone["lower"]
        self.session       = zone["session"]
        self.color         = zone["color"]
        self.zone_id       = zone_id
        self.direction     = direction
        self.entry_price   = entry_price
        self.sl            = sl
        self.target        = target
        self.entry_time    = entry_time
        self.option_strike = option_strike
        self.option_sym    = option_sym
        self.option_entry  = option_entry

        self.risk        = abs(entry_price - sl)
        if direction == "CE":
            self.lot1_target = entry_price + self.risk
        else:
            self.lot1_target = entry_price - self.risk
        self.lot1_hit = False

    def check_exit(self, ltp: float) -> str | None:
        # Lot 1: 1:1 target (only before it's been booked)
        if not self.lot1_hit:
            if self.direction == "CE" and ltp >= self.lot1_target:
                return "LOT1_TARGET"
            if self.direction == "PE" and ltp <= self.lot1_target:
                return "LOT1_TARGET"
        # Lot 2: use CTC (entry) as SL after lot1 is booked
        eff_sl = self.entry_price if self.lot1_hit else self.sl
        if self.direction == "CE":
            if ltp <= eff_sl:      return "SL_HIT"
            if ltp >= self.target: return "TARGET_HIT"
        else:
            if ltp >= eff_sl:      return "SL_HIT"
            if ltp <= self.target: return "TARGET_HIT"
        return None

    def to_row(self, exit_price, exit_reason, exit_time, option_exit, lot: int | str = 2) -> dict:
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
            "lot":           lot,
        }


def next_target(zones, tapped_zone, direction) -> float | None:
    if direction == "CE":
        above = [z for z in zones if z["lower"] > tapped_zone["upper"] + 5]
        return above[0]["lower"] if above else None
    below = [z for z in zones if z["upper"] < tapped_zone["lower"] - 5]
    return below[-1]["upper"] if below else None


# ── main ──────────────────────────────────────────────────────────────────────
async def run(state: dict | None = None) -> None:
    # Populate zones immediately so dashboard shows them even before token is set
    if state is not None:
        try:
            _cfg_early = load_config()
            state["zones_cfg"] = [
                {"id": z["id"], "upper": z["upper"], "lower": z["lower"],
                 "type": z.get("type", "neutral"), "session": z["session"]}
                for z in _cfg_early["_zones"]
            ]
        except Exception:
            pass

    # If running under server.py with no token yet, wait until one is pasted
    if state is not None and not state.get("token"):
        print("[INFO] No token — waiting (paste via Settings on the dashboard)…")
        while not state.get("token"):
            await asyncio.sleep(3)
        print("[INFO] Token received, starting…")

    cfg           = load_config()
    fyers         = load_fyers(
        token=state["token"] if state else None,
        app_id=state["app_id"] if state else None,
    )
    zones         = cfg["_zones"]
    if state is not None:
        state["zones_cfg"] = [
            {"id": z["id"], "upper": z["upper"], "lower": z["lower"],
             "type": z.get("type", "neutral"), "session": z["session"]}
            for z in zones
        ]
    cooldown_s    = int(cfg.get("cooldown_min", 30)) * 60
    bounce_buffer = float(cfg.get("bounce_buffer", 5))
    max_sl_pts    = float(cfg.get("max_sl_pts", 30))
    reversal_pts  = float(cfg.get("reversal_pts", 20))
    breach_pts    = float(cfg.get("breach_pts", 50))
    poll_s        = 30

    ensure_csv()

    print(f"\nLVN Mock Trader  —  {FUT_SYMBOL}  (Fyers)")
    print(f"Zones ({len(zones)}):")
    for z in sorted(zones, key=lambda x: -x["mid"]):
        ztype = z.get("type", "neutral")
        print(f"  {z['color']:4s} {z['session']}  {z['upper']:.2f} / {z['lower']:.2f}  [{ztype:7s}]  ({z['date']})")
    print(f"\nCooldown: {cooldown_s//60} min  |  Poll: {poll_s}s  |  Log: {daily_csv()}\n")

    active: Trade | None     = None
    tapped: dict[str, datetime]    = {}
    zone_state: dict[str, str]     = {}
    zone_extreme: dict[str, float] = {}   # high for PE, low for CE
    zone_trades: dict[str, int]    = {}   # completed trades per zone (max 2/day)
    zone_breached: dict[str, bool] = {}   # supply/demand zones neutralized by breach
    trade_date: str                = now_ist().strftime("%Y-%m-%d")
    prev_ltp: float | None         = None

    # pre-seed any force_tap entries from config
    for ft in cfg.get("force_tap", []):
        fu, fl, fd = float(ft[0]), float(ft[1]), ft[2].upper()
        for i, z in enumerate(zones):
            if z["upper"] == fu and z["lower"] == fl:
                zone_state[i] = f"tapped_{fd.lower()}"
                print(f"  [FORCE] zone {fu:.0f}/{fl:.0f} pre-seeded as tapped_{fd.lower()}")

    while True:
        t = now_ist()

        # ── new-day reset ─────────────────────────────────────────────────────
        today = t.strftime("%Y-%m-%d")
        if today != trade_date:
            trade_date = today
            zone_trades.clear()
            zone_breached.clear()
            ensure_csv()
            if state is not None:
                state["events"] = []
                state["candles"] = []
                state["trades"] = []
            print(f"  [INFO] New day {today} — zone trade counts reset  |  Log: {daily_csv()}")

        # ── EOD flat ──────────────────────────────────────────────────────────
        if active and t.time() >= MCX_CLOSE:
            syms = [FUT_SYMBOL] + ([active.option_sym] if active.option_sym else [])
            prices   = await fetch_prices(fyers, syms)
            ltp      = prices.get(FUT_SYMBOL, active.entry_price)
            opt_exit = prices.get(active.option_sym) if active.option_sym else None
            if active.lot1_hit:
                # Lot1 already booked; close lot2 at EOD
                row = active.to_row(ltp, "EOD_FLAT", t, opt_exit, lot=2)
                print(f"\n[{t:%H:%M}] EOD FLAT lot2 {active.direction} fut {ltp:.0f}  "
                      f"fut_pnl {row['fut_pnl_pts']:+.0f}  opt_pnl {row['option_pnl'] or '-'}")
            else:
                # Both lots still open; close both at same price
                row = active.to_row(ltp, "EOD_FLAT", t, opt_exit, lot="1+2")
                print(f"\n[{t:%H:%M}] EOD FLAT both lots {active.direction} fut {ltp:.0f}  "
                      f"fut_pnl {row['fut_pnl_pts']:+.0f}  opt_pnl {row['option_pnl'] or '-'}")
            append_trade(row, state)
            if state is not None:
                state["events"].append({
                    "time":      int(datetime.now(timezone.utc).timestamp()),
                    "type":      "exit",
                    "direction": active.direction,
                    "price":     ltp,
                    "text":      f"EOD{float(row['fut_pnl_pts']):+.0f}",
                })
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

        if state is not None:
            unix_ts   = int(datetime.now(timezone.utc).timestamp())
            candle_ts = (unix_ts // 60) * 60
            candles   = state["candles"]
            if not candles or candles[-1]["time"] != candle_ts:
                candles.append({"time": candle_ts, "open": ltp, "high": ltp, "low": ltp, "close": ltp})
            else:
                c = candles[-1]
                if ltp > c["high"]: c["high"] = ltp
                if ltp < c["low"]:  c["low"]  = ltp
                c["close"] = ltp
            if len(candles) > 300:
                state["candles"] = candles[-300:]
            state["zone_trades"]   = dict(zone_trades)
            state["zone_breached"] = dict(zone_breached)

        # ── breach scan ───────────────────────────────────────────────────────
        for zone in zones:
            zid   = zone["id"]
            ztype = zone.get("type", "neutral")
            if ztype == "neutral" or zone_breached.get(zid):
                continue
            if ztype == "supply" and ltp > zone["upper"] + breach_pts:
                zone_breached[zid] = True
                print(f"  !! {zone['color']} {zone['session']} {zone['upper']:.0f}/{zone['lower']:.0f} "
                      f"supply breached +{breach_pts:.0f}pts → NEUTRAL  (CE + PE now allowed)")
            elif ztype == "demand" and ltp < zone["lower"] - breach_pts:
                zone_breached[zid] = True
                print(f"  !! {zone['color']} {zone['session']} {zone['upper']:.0f}/{zone['lower']:.0f} "
                      f"demand breached -{breach_pts:.0f}pts → NEUTRAL  (CE + PE now allowed)")

        # ── status line ───────────────────────────────────────────────────────
        if active:
            icon    = "▲" if active.direction == "CE" else "▼"
            opt_str = f"  opt {opt_ltp:.0f}" if opt_ltp else ""
            ctc_tag = "  [CTC]" if active.lot1_hit else ""
            line = (f"[{t:%H:%M:%S}]  fut {ltp:8.2f}{opt_str}  "
                    f"{icon}{active.direction} SL {active.sl:.2f}  T {active.target:.2f}{ctc_tag}")
            if state is not None:
                state["price"] = ltp
                state["trade"] = {
                    "direction":   active.direction,
                    "entry":       active.entry_price,
                    "sl":          active.sl,
                    "target":      active.target,
                    "lot1_hit":    active.lot1_hit,
                    "lot1_target": active.lot1_target,
                    "risk":        active.risk,
                }
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
            breach_pts    = float(cfg.get("breach_pts", 50))
            zone_state.clear()
            zone_extreme.clear()
            zone_breached.clear()
            # zone_trades and tapped intentionally preserved — id-keyed, survive boundary edits
            state["zones_cfg"] = [
                {"id": z["id"], "upper": z["upper"], "lower": z["lower"],
                 "type": z.get("type", "neutral"), "session": z["session"]}
                for z in zones
            ]
            state["events"] = []
            state["levels_updated"] = False
            print(f"  [INFO] Zones reloaded ({len(zones)} zones)")

        # ── check exit ────────────────────────────────────────────────────────
        if active:
            reason = active.check_exit(ltp)
            if reason == "LOT1_TARGET":
                # Book lot1 at 1:1; move lot2 SL to entry (CTC)
                row = active.to_row(active.lot1_target, "LOT1_TARGET", t, opt_ltp, lot=1)
                print(f"\n  ★ LOT1 BOOKED  {active.color} {active.session} {active.direction}  "
                      f"entry {active.entry_price:.2f} → {active.lot1_target:.2f}  "
                      f"+{active.risk:.0f}pts  |  LOT2 SL → CTC {active.entry_price:.2f}\n")
                append_trade(row, state)
                active.sl = active.entry_price   # update display SL to CTC
                if state is not None:
                    state["events"].append({
                        "time":      int(datetime.now(timezone.utc).timestamp()),
                        "type":      "lot1",
                        "direction": active.direction,
                        "price":     active.lot1_target,
                        "text":      f"L1+{active.risk:.0f}",
                    })
            elif reason in ("SL_HIT", "TARGET_HIT"):
                row  = active.to_row(ltp, reason, t, opt_ltp, lot=2)
                mark = "✓" if float(row["fut_pnl_pts"]) > 0 else "✗"
                print(f"\n  {mark} {reason} lot2  {active.color} {active.session} {active.direction}  "
                      f"entry {active.entry_price:.2f} → {ltp:.2f}  "
                      f"fut {row['fut_pnl_pts']:+}pts  opt {row['option_pnl'] or '-'}\n")
                append_trade(row, state)
                if state is not None:
                    state["events"].append({
                        "time":      int(datetime.now(timezone.utc).timestamp()),
                        "type":      "exit",
                        "direction": active.direction,
                        "price":     ltp,
                        "text":      f"{reason[:2]} {float(row['fut_pnl_pts']):+.0f}",
                    })
                active = None

        # ── zone tap / reaction ───────────────────────────────────────────────
        if active is None and prev_ltp is not None:
            for i, zone in enumerate(zones):
                upper, lower = zone["upper"], zone["lower"]
                zid = zone["id"]

                last = tapped.get(zid)
                if last and (t - last).total_seconds() < cooldown_s:
                    continue

                if zone_trades.get(zid, 0) >= 2:
                    continue   # this zone has used its 2 trades today

                st = zone_state.get(zid, "idle")

                if st == "idle":
                    in_zone  = lower <= ltp      <= upper
                    was_out  = not (lower <= prev_ltp <= upper)
                    if in_zone and was_out:
                        ztype      = zone.get("type", "neutral")
                        is_neutral = zone_breached.get(zid, False) or ztype == "neutral"
                        if prev_ltp > upper:
                            if ztype == "supply" and not is_neutral:
                                pass   # CE tap on supply zone — ignore until breached
                            else:
                                zone_state[zid]  = "tapped_ce"
                                zone_extreme[zid] = ltp
                                tag = f" [{ztype}]" if ztype != "neutral" else ""
                                print(f"  ◎ TAPPED CE  {zone['color']} {zone['session']}{tag}  "
                                      f"{upper:.0f}/{lower:.0f} — waiting for bounce above {upper + bounce_buffer:.2f}")
                        elif prev_ltp < lower:
                            if ztype == "demand" and not is_neutral:
                                pass   # PE tap on demand zone — ignore until breached
                            else:
                                zone_state[zid]  = "tapped_pe"
                                zone_extreme[zid] = ltp
                                tag = f" [{ztype}]" if ztype != "neutral" else ""
                                print(f"  ◎ TAPPED PE  {zone['color']} {zone['session']}{tag}  "
                                      f"{upper:.0f}/{lower:.0f} — waiting for fade below {lower - bounce_buffer:.2f}")
                    continue

                entry_model = None

                if st == "tapped_ce":
                    zone_extreme[zid] = min(zone_extreme.get(zid, ltp), ltp)   # track lowest inside
                    if ltp >= upper + bounce_buffer:
                        direction   = "CE"
                        entry_model = "boundary"
                    elif ltp >= zone_extreme[zid] + reversal_pts:
                        direction   = "CE"
                        entry_model = "reversal"
                    elif ltp < lower:
                        zone_state[zid]   = "idle"
                        zone_extreme.pop(zid, None)
                        print(f"  ✗ ZONE FAILED  {zone['session']} {upper:.0f}/{lower:.0f} — broke below")
                        continue
                    else:
                        continue

                elif st == "tapped_pe":
                    zone_extreme[zid] = max(zone_extreme.get(zid, ltp), ltp)   # track highest inside
                    if ltp < lower - bounce_buffer:
                        direction   = "PE"
                        entry_model = "boundary"
                    elif ltp <= zone_extreme[zid] - reversal_pts:
                        direction   = "PE"
                        entry_model = "reversal"
                    elif ltp > upper:
                        zone_state[zid]   = "idle"
                        zone_extreme.pop(zid, None)
                        print(f"  ✗ ZONE FAILED  {zone['session']} {upper:.0f}/{lower:.0f} — broke above")
                        continue
                    else:
                        continue
                else:
                    continue

                # confirmed → compute SL from zone extreme (lowest inside for CE, highest for PE)
                if direction == "CE":
                    sl   = zone_extreme[zid] - bounce_buffer
                    risk = ltp - sl
                else:
                    sl   = zone_extreme[zid] + bounce_buffer
                    risk = sl - ltp

                # skip if risk exceeds max_sl_pts
                if risk > max_sl_pts:
                    zone_state[zid] = "idle"
                    zone_extreme.pop(zid, None)
                    tapped[zid] = t
                    print(f"  [SKIP] {zone['session']} {upper:.0f}/{lower:.0f} {direction} "
                          f"— risk {risk:.0f}pts > {max_sl_pts:.0f}pts max")
                    continue
                target = next_target(zones, zone, direction)
                tapped[zid]     = t
                zone_state[zid] = "idle"
                zone_extreme.pop(zid, None)

                if target is None:
                    print(f"  [SKIP] {zone['session']} {upper:.0f}/{lower:.0f} {direction} — no target")
                    continue

                opt_strike, opt_sym, opt_premium = None, None, None
                opt_strike, opt_sym = itm_symbol(ltp, direction)
                opt_prices  = await fetch_prices(fyers, [opt_sym])
                opt_premium = opt_prices.get(opt_sym)

                active = Trade(zone, zid, direction, ltp, sl, target, t,
                               opt_strike, opt_sym, opt_premium)
                if state is not None:
                    state["events"].append({
                        "time":      int(datetime.now(timezone.utc).timestamp()),
                        "type":      "entry",
                        "direction": direction,
                        "price":     ltp,
                        "text":      f"{direction} {ltp:.0f}",
                    })

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
