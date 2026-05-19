#!/usr/bin/env python3
"""
Generate an HTML billing report from tracked sessions.

Usage:
  report.py                    Report for the current week
  report.py --today            Report for today
  report.py --week             Report for the current week (default)
  report.py --month            Report for the current month
  report.py --since 2024-01-01 Report from a date
  report.py --client "Acme"    Filter by client name
  report.py --rate 500         Override TJM (€/day) for all clients
  report.py --open             Open the report in the browser after generating
"""

import argparse
import json
import subprocess
import sys
import webbrowser
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "timetracker"
SESSIONS_FILE = DATA_DIR / "sessions.json"
CLIENTS_FILE = DATA_DIR / "clients.json"
REPORTS_DIR = DATA_DIR / "reports"


def load_sessions() -> list:
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except Exception:
        return []


def load_clients() -> dict[str, dict]:
    try:
        return {c["name"]: c for c in json.loads(CLIENTS_FILE.read_text())}
    except Exception:
        return {}


def session_duration(s: dict) -> float:
    try:
        start = datetime.fromisoformat(s["start"])
        end = datetime.fromisoformat(s["end"]) if s.get("end") else datetime.now().astimezone()
        return max(0.0, (end - start).total_seconds())
    except Exception:
        return 0.0


def fmt_h(seconds: float) -> str:
    h = seconds / 3600
    return f"{h:.2f}h"


def fmt_hm(seconds: float) -> str:
    m = int(seconds) // 60
    return f"{m // 60}h{m % 60:02d}"


def filter_sessions(sessions: list, start_date: date, end_date: date, client: str | None) -> list:
    result = []
    for s in sessions:
        try:
            d = date.fromisoformat(s["start"][:10])
        except Exception:
            continue
        if not (start_date <= d <= end_date):
            continue
        if client and s.get("client", "") != client:
            continue
        result.append(s)
    return result


def generate_html(sessions: list, clients: dict, period_label: str, rate_override: float | None) -> str:
    # Group by client → day → app
    by_client: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(float))
    )
    client_totals: dict[str, float] = defaultdict(float)
    day_set: set[str] = set()

    for s in sessions:
        client = s.get("client") or "Sans client"
        day = s["start"][:10]
        app = s.get("app", "?")
        dur = session_duration(s)
        by_client[client][day][app] += dur
        client_totals[client] += dur
        day_set.add(day)

    sorted_days = sorted(day_set)
    grand_total = sum(client_totals.values())

    # Build client cards HTML
    client_cards = ""
    for client_name in sorted(client_totals, key=lambda k: -client_totals[k]):
        total_secs = client_totals[client_name]
        client_info = clients.get(client_name, {})
        color = client_info.get("color", "#4fc3f7")
        rate = rate_override if rate_override is not None else client_info.get("rate", 0)
        days_worked = total_secs / 3600 / 8
        billing = days_worked * rate if rate else 0

        # Day breakdown rows
        day_rows = ""
        for day in sorted_days:
            if day not in by_client[client_name]:
                continue
            day_secs = sum(by_client[client_name][day].values())
            app_breakdown = ", ".join(
                f"<span class='app-chip'>{app} {fmt_hm(dur)}</span>"
                for app, dur in sorted(by_client[client_name][day].items(), key=lambda x: -x[1])
            )
            day_rows += f"""
            <tr>
              <td>{datetime.strptime(day, '%Y-%m-%d').strftime('%a %d %b')}</td>
              <td class='num'>{fmt_hm(day_secs)}</td>
              <td class='num dim'>{day_secs/3600:.2f}h</td>
              <td class='apps'>{app_breakdown}</td>
            </tr>"""

        billing_row = (
            f"<div class='billing'>≈ {billing:.0f} € <span class='dim'>({days_worked:.2f}j × {rate} €/j)</span></div>"
            if rate else ""
        )

        client_cards += f"""
        <div class='client-card' style='border-left: 4px solid {color}'>
          <div class='client-header'>
            <span class='client-dot' style='background:{color}'></span>
            <span class='client-name'>{client_name}</span>
            <span class='client-total'>{fmt_hm(total_secs)}</span>
          </div>
          {billing_row}
          <table class='day-table'>
            <thead><tr><th>Jour</th><th>Durée</th><th></th><th>Applications</th></tr></thead>
            <tbody>{day_rows}</tbody>
          </table>
        </div>"""

    # Summary table
    summary_rows = ""
    for client_name in sorted(client_totals, key=lambda k: -client_totals[k]):
        total_secs = client_totals[client_name]
        pct = total_secs / grand_total * 100 if grand_total else 0
        client_info = clients.get(client_name, {})
        color = client_info.get("color", "#4fc3f7")
        rate = rate_override if rate_override is not None else client_info.get("rate", 0)
        billing = (total_secs / 3600 / 8) * rate if rate else 0
        billing_cell = f"{billing:.0f} €" if rate else "—"
        summary_rows += f"""
        <tr>
          <td><span class='dot' style='background:{color}'></span> {client_name}</td>
          <td class='num'>{fmt_hm(total_secs)}</td>
          <td class='num dim'>{pct:.0f}%</td>
          <td class='num'>{billing_cell}</td>
        </tr>"""

    generated_at = datetime.now().strftime("%d/%m/%Y à %H:%M")

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rapport — {period_label}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: #0f0f0f; color: #e0e0e0; padding: 32px 24px; line-height: 1.5; }}
  h1 {{ font-size: 24px; font-weight: 700; margin-bottom: 4px; color: #fff; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 32px; }}
  .summary-card {{ background: #1a1a1a; border-radius: 10px; padding: 20px;
                   margin-bottom: 32px; border: 1px solid #2a2a2a; }}
  .summary-card h2 {{ font-size: 14px; color: #888; text-transform: uppercase;
                       letter-spacing: .5px; margin-bottom: 16px; }}
  .grand-total {{ font-size: 36px; font-weight: 700; color: #4fc3f7; margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; color: #666; font-size: 12px; text-transform: uppercase;
        letter-spacing: .5px; padding: 6px 8px; border-bottom: 1px solid #2a2a2a; }}
  td {{ padding: 8px; border-bottom: 1px solid #1e1e1e; font-size: 14px; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .dim {{ color: #666; }}
  .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%;
           margin-right: 6px; vertical-align: middle; }}
  .client-card {{ background: #1a1a1a; border-radius: 10px; padding: 20px;
                  margin-bottom: 20px; border: 1px solid #2a2a2a; }}
  .client-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }}
  .client-dot {{ width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }}
  .client-name {{ font-size: 18px; font-weight: 600; flex: 1; }}
  .client-total {{ font-size: 20px; font-weight: 700; color: #4fc3f7; }}
  .billing {{ font-size: 14px; color: #81c784; margin-bottom: 16px; font-weight: 500; }}
  .day-table th {{ font-size: 11px; }}
  .apps {{ font-size: 12px; }}
  .app-chip {{ display: inline-block; background: #252525; border-radius: 4px;
               padding: 2px 6px; margin: 2px; color: #bbb; }}
  h2.section {{ font-size: 14px; color: #888; text-transform: uppercase;
                 letter-spacing: .5px; margin-bottom: 16px; margin-top: 32px; }}
  @media print {{
    body {{ background: #fff; color: #000; }}
    .client-card, .summary-card {{ border: 1px solid #ccc; background: #fff; }}
    .app-chip {{ background: #eee; color: #333; }}
  }}
</style>
</head>
<body>
  <h1>Rapport — {period_label}</h1>
  <p class="meta">Généré le {generated_at}</p>

  <div class="summary-card">
    <h2>Résumé</h2>
    <div class="grand-total">{fmt_hm(grand_total)}</div>
    <table>
      <thead><tr><th>Client</th><th style="text-align:right">Durée</th><th style="text-align:right">%</th><th style="text-align:right">Facturation</th></tr></thead>
      <tbody>{summary_rows}</tbody>
    </table>
  </div>

  <h2 class="section">Détail par client</h2>
  {client_cards if client_cards else '<p style="color:#666">Aucune session sur cette période.</p>'}
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate time tracking HTML report")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--today", action="store_true")
    group.add_argument("--week", action="store_true")
    group.add_argument("--month", action="store_true")
    group.add_argument("--since", metavar="DATE", help="Start date YYYY-MM-DD")
    parser.add_argument("--client", metavar="NAME", help="Filter by client")
    parser.add_argument("--rate", type=float, metavar="EUR", help="Override TJM for all clients")
    parser.add_argument("--open", dest="open_browser", action="store_true",
                        help="Open report in browser")
    parser.add_argument("--out", metavar="FILE", help="Output file path")
    args = parser.parse_args()

    today = date.today()

    if args.today:
        start_date = end_date = today
        period_label = f"Aujourd'hui — {today.strftime('%d %b %Y')}"
    elif args.month:
        start_date = today.replace(day=1)
        end_date = today
        period_label = today.strftime("%B %Y")
    elif args.since:
        start_date = date.fromisoformat(args.since)
        end_date = today
        period_label = f"{start_date.strftime('%d %b')} → {today.strftime('%d %b %Y')}"
    else:  # default: week
        start_date = today - timedelta(days=today.weekday())
        end_date = today
        period_label = f"Semaine du {start_date.strftime('%d %b %Y')}"

    sessions = load_sessions()
    clients = load_clients()
    filtered = filter_sessions(sessions, start_date, end_date, args.client)
    html = generate_html(filtered, clients, period_label, args.rate)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.out:
        out_path = Path(args.out)
    else:
        slug = period_label.lower().replace(" ", "-").replace("→", "to").replace("'", "")
        out_path = REPORTS_DIR / f"rapport-{slug}.html"

    out_path.write_text(html, encoding="utf-8")
    print(f"Rapport généré : {out_path}")

    if args.open_browser:
        webbrowser.open(f"file://{out_path.resolve()}")

    return str(out_path)


if __name__ == "__main__":
    main()
