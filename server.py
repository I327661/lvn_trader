"""
Render-compatible wrapper for LVN alert.py.
- Runs trading loop in a background thread
- Exposes /token to update Fyers token daily
- Exposes /levels to edit zones each day
- Exposes /status for current price/trade
- Exposes / for a simple dashboard
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from flask import Flask, request, redirect

app = Flask(__name__)

LEVELS_FILE = Path(__file__).resolve().parent / "levels" / "lvn_levels.json"

# Shared state between Flask and trading thread
state: dict = {
    "app_id":          os.getenv("FYERS_APP_ID", "POGBT26YG3-100"),
    "token":           os.getenv("FYERS_ACCESS_TOKEN", ""),
    "token_updated":   False,
    "levels_updated":  False,
    "price":           None,
    "trade":           None,
    "log":             [],   # last 50 lines
}


# ── web endpoints ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    trade_html = ""
    if state["trade"]:
        t = state["trade"]
        trade_html = f"<b>{t['direction']}</b> entry {t['entry']} SL {t['sl']} T {t['target']}"
    else:
        trade_html = "Flat"

    log_html = "<br>".join(state["log"][-30:])
    price = state["price"] or "—"

    return f"""<!doctype html>
<html><head><title>LVN Trader</title>
<meta http-equiv="refresh" content="30">
<style>body{{font-family:monospace;background:#111;color:#eee;padding:20px}}
input,textarea{{width:600px;padding:6px}} button{{padding:6px 16px}}
a{{color:#7af}} hr{{border-color:#333}}</style>
</head><body>
<h2>LVN Mock Trader — CRUDEOIL</h2>
<p>Price: <b>{price}</b> &nbsp;|&nbsp; Trade: {trade_html}</p>
<p><a href="/levels">Edit Zones</a></p>
<hr>
<form method="post" action="/token">
  <b>Update Fyers Token (daily):</b><br><br>
  <input name="token" placeholder="Paste access token here">
  <button type="submit">Update</button>
</form>
<hr>
<pre style="font-size:12px">{log_html}</pre>
</body></html>"""


@app.route("/token", methods=["POST"])
def update_token():
    token = request.form.get("token", "").strip()
    if token:
        state["token"] = token
        state["token_updated"] = True
        return redirect("/")
    return "No token provided", 400


@app.route("/levels", methods=["GET", "POST"])
def levels():
    if request.method == "POST":
        raw = request.form.get("levels_json", "").strip()
        try:
            cfg = json.loads(raw)
            LEVELS_FILE.write_text(raw)
            state["levels_updated"] = True
            return redirect("/")
        except json.JSONDecodeError as e:
            error = f"Invalid JSON: {e}"
            return _levels_page(raw, error)
    # GET — show current levels
    current = LEVELS_FILE.read_text() if LEVELS_FILE.exists() else "{}"
    return _levels_page(current)


def _levels_page(current_json: str, error: str = "") -> str:
    err_html = f'<p style="color:#f66">{error}</p>' if error else ""
    return f"""<!doctype html>
<html><head><title>Edit Zones</title>
<style>body{{font-family:monospace;background:#111;color:#eee;padding:20px}}
textarea{{width:700px;height:400px;padding:8px;background:#222;color:#eee;border:1px solid #444}}
button{{padding:8px 20px;margin-top:10px}} a{{color:#7af}}</style>
</head><body>
<h2>Edit Zones</h2>
<p><a href="/">Back to dashboard</a></p>
{err_html}
<form method="post">
  <textarea name="levels_json">{current_json}</textarea><br>
  <button type="submit">Update Zones</button>
</form>
</body></html>"""


@app.route("/status")
def status():
    from flask import jsonify
    return jsonify({"price": state["price"], "trade": state["trade"]})


@app.route("/health")
def health():
    return "ok"


# ── trading thread ─────────────────────────────────────────────────────────────
def _run_trading():
    from alert import run
    asyncio.run(run(state=state))


def start_trading():
    t = threading.Thread(target=_run_trading, daemon=True)
    t.start()


# ── entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not state["token"]:
        print("[WARN] FYERS_ACCESS_TOKEN env var not set — update via /token after deploy")
    start_trading()
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

