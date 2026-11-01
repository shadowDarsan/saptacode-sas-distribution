import base64
import hmac
import html
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from db.schema import DEFAULT_DB_PATH, DEFAULT_OUTPUT_DIR, get_connection, init_db, utc_now_iso
from modules.auditor import audit_prospect
from modules.delivery_engine import DELIVERIES_DIR, generate_monday_roi_report
from modules.dispatcher import build_mailto_link, dispatch_campaign, record_ses_tracker_webhook
from modules.radar import harvest_signals
from modules.wedge_generator import (
    OUTPUT_DIR,
    build_campaign_for_prospect,
    generate_satellite_micro_tool,
)

DAEMON_STATE: Dict[str, Any] = {
    "last_run": "Never",
    "cycles_completed": 0,
    "interval_seconds": 900,
    "running": False,
}


def execute_full_cycle(config: Dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> Dict[str, int]:
    """Executes one complete 15-minute harvest -> audit -> email extract -> wedge cycle."""
    init_db(db_path)
    stats = harvest_signals(db_path)

    with get_connection(db_path) as conn:
        prospects = conn.execute("SELECT id FROM prospects").fetchall()

    audited_count = 0
    campaigns_built = 0
    for p in prospects:
        pid = p["id"]
        with get_connection(db_path) as conn:
            existing = conn.execute("SELECT id FROM campaigns WHERE prospect_id = ?", (pid,)).fetchone()
        if existing:
            continue
        audit_prospect(pid, db_path)
        audited_count += 1
        build_campaign_for_prospect(pid, config, db_path, OUTPUT_DIR)
        campaigns_built += 1

    generate_satellite_micro_tool(config, OUTPUT_DIR)
    DAEMON_STATE["last_run"] = utc_now_iso()
    DAEMON_STATE["cycles_completed"] += 1
    return {
        "scanned": stats["scanned"],
        "audited": audited_count,
        "new_campaigns": campaigns_built,
    }


def background_cron_worker(config: Dict[str, Any], interval_sec: int) -> None:
    DAEMON_STATE["running"] = True
    DAEMON_STATE["interval_seconds"] = interval_sec
    while True:
        try:
            execute_full_cycle(config, DEFAULT_DB_PATH)
        except Exception as exc:
            DAEMON_STATE["last_error"] = str(exc)
        time.sleep(interval_sec)


def render_dashboard_html(config: Dict[str, Any], flash_msg: str = "") -> str:
    init_db(DEFAULT_DB_PATH)
    ses_url = os.environ.get("SES_WEBHOOK_URL") or config.get("email_dispatch", {}).get("ses_webhook_url", "")
    ses_status_text = f"Connected ({ses_url})" if ses_url else "Outbox Mode (Set SES_WEBHOOK_URL env var)"

    with get_connection(DEFAULT_DB_PATH) as conn:
        campaigns = conn.execute(
            """
            SELECT c.*, p.company_name, p.slug, p.app_url, p.founder_handle, p.contact_email, p.social_url, a.evidence_id, a.total_issues
            FROM campaigns c
            JOIN prospects p ON p.id = c.prospect_id
            JOIN audits a ON a.id = c.audit_id
            ORDER BY c.id DESC
            """
        ).fetchall()
        deliveries = conn.execute(
            """
            SELECT d.*, p.company_name, p.slug
            FROM deliveries d
            JOIN prospects p ON p.id = d.prospect_id
            ORDER BY d.id DESC
            """
        ).fetchall()

    rows_html = ""
    for c in campaigns:
        cid = c["id"]
        pid = c["prospect_id"]
        company = html.escape(c["company_name"])
        app_url = html.escape(c["app_url"])
        to_email = c["contact_email"] or "founders@startup.io"
        social_url = c["social_url"] or f"https://x.com/{c['founder_handle'].lstrip('@')}"
        subject = c["outreach_subject"] or f"Quick bug flag on {c['company_name']} mobile flow"
        body = c["outreach_message"]
        mailto_href = build_mailto_link(to_email, subject, body)
        x_dm_text = urllib.parse.quote(body)
        x_intent_href = f"https://x.com/intent/tweet?text={x_dm_text}"
        tracker = html.escape(c["tracker_status"] if "tracker_status" in c.keys() else "QUEUED")

        status_badge = (
            f'<span class="badge badge-sent">✓ {tracker}</span>'
            if c["status"] == "SENT"
            else '<span class="badge badge-pending">● PENDING APPROVAL</span>'
        )

        rows_html += f"""
        <div class="card">
          <div class="card-top">
            <div>
              <strong style="font-size:1.15rem;">#{cid} · {company}</strong>
              <a href="{app_url}" target="_blank" style="color:#60a5fa;margin-left:0.5rem;font-size:0.88rem;">{app_url} ↗</a>
              <span class="ev-tag">Evidence: {html.escape(c['evidence_id'])} ({c['total_issues']} bugs)</span>
            </div>
            <div>{status_badge}</div>
          </div>

          <div class="contact-bar">
            <span>📧 <strong>Email:</strong> <code>{html.escape(to_email)}</code></span>
            <span>🐦 <strong>X/Twitter:</strong> <a href="{html.escape(social_url)}" target="_blank" style="color:#93c5fd;">{html.escape(c['founder_handle'])} ↗</a></span>
            <span>🧠 <strong>MiroFish Score:</strong> <strong style="color:#10b981;">{c['mirofish_score']}/100</strong></span>
          </div>

          <div class="msg-box">{html.escape(body)}</div>

          <div class="btn-row">
            <a class="btn btn-secondary" href="/report/{html.escape(c['slug'])}-audit.html" target="_blank">📄 View Crime Scene Page</a>
            <form method="POST" action="/approve/{cid}" style="display:inline;">
              <button type="submit" class="btn btn-green">🚀 Approve &amp; POST to Amazon SES API</button>
            </form>
            <a class="btn btn-blue" href="{html.escape(mailto_href)}">✉️ 1-Click Mail App</a>
            <a class="btn btn-secondary" href="{html.escape(x_intent_href)}" target="_blank">🐦 1-Click X DM</a>
            <form method="POST" action="/deliver/{pid}" style="display:inline;">
              <button type="submit" class="btn btn-purple">💰 Generate $2.5k/mo Monday ROI Report</button>
            </form>
          </div>
        </div>
        """

    deliveries_html = ""
    for d in deliveries:
        deliveries_html += f"""
        <div class="delivery-item">
          <span><strong>{html.escape(d['company_name'])}</strong> · {html.escape(d['week_label'])} · {d['regressions_caught']} Regressions Patched · {d['poached_leads_count']} Competitor Leads Poached</span>
          <a class="btn btn-secondary" href="/delivery-report/{html.escape(d['slug'])}-monday-roi.html" target="_blank">Open Monday ROI Report ↗</a>
        </div>
        """

    flash_banner = f'<div class="flash">{html.escape(flash_msg)}</div>' if flash_msg else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>AegisSaS · Production Railway Command Center</title>
  <style>
    body {{ background:#090a0f; color:#f3f4f6; font-family:-apple-system,BlinkMacSystemFont,"Inter",sans-serif; margin:0; padding:1.75rem; line-height:1.5; }}
    .wrap {{ max-width:1100px; margin:0 auto; }}
    .top {{ display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #232736; padding-bottom:1rem; margin-bottom:1.5rem; flex-wrap:wrap; gap:1rem; }}
    .cron-box {{ background:#12141d; border:1px solid #10b981; border-radius:8px; padding:0.6rem 1rem; font-size:0.82rem; }}
    .flash {{ background:#064e3b; border:1px solid #10b981; color:#a7f3d0; padding:0.85rem 1.1rem; border-radius:8px; margin-bottom:1.25rem; font-weight:600; }}
    .card {{ background:#12141d; border:1px solid #232736; border-radius:10px; padding:1.25rem; margin-bottom:1rem; }}
    .card-top {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:0.6rem; flex-wrap:wrap; gap:0.5rem; }}
    .ev-tag {{ font-family:monospace; font-size:0.76rem; background:#1b1f2e; padding:0.2rem 0.5rem; border-radius:4px; margin-left:0.5rem; color:#9ca3af; }}
    .contact-bar {{ display:flex; gap:1.5rem; font-size:0.84rem; color:#d1d5db; margin-bottom:0.75rem; flex-wrap:wrap; }}
    .msg-box {{ background:#06070a; border:1px solid #232736; border-radius:6px; padding:0.85rem; font-size:0.88rem; color:#a7f3d0; margin-bottom:0.9rem; font-family:ui-monospace,monospace; }}
    .btn-row {{ display:flex; gap:0.6rem; flex-wrap:wrap; }}
    .btn {{ text-decoration:none; border:none; padding:0.55rem 0.9rem; border-radius:6px; font-size:0.82rem; font-weight:600; cursor:pointer; display:inline-block; }}
    .btn-green {{ background:#10b981; color:#04130d; }}
    .btn-blue {{ background:#2563eb; color:#fff; }}
    .btn-purple {{ background:#7c3aed; color:#fff; }}
    .btn-secondary {{ background:#1f2433; color:#e5e7eb; border:1px solid #32394e; }}
    .badge {{ font-size:0.72rem; font-weight:700; padding:0.25rem 0.6rem; border-radius:99px; }}
    .badge-pending {{ background:#78350f; color:#fcd34d; }}
    .badge-sent {{ background:#064e3b; color:#6ee7b7; }}
    .delivery-item {{ display:flex; justify-content:space-between; align-items:center; background:#12141d; border:1px solid #232736; padding:0.85rem 1.1rem; border-radius:8px; margin-bottom:0.6rem; font-size:0.9rem; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1 style="margin:0;font-size:1.5rem;">⚡ AegisSaS · Railway Production Command Center</h1>
        <span style="color:#9ca3af;font-size:0.84rem;">Amazon SES API: <code>{html.escape(ses_status_text)}</code></span>
      </div>
      <div class="cron-box">
        🟢 <strong>15-Min Auto-Daemon</strong> · Cycles: {DAEMON_STATE['cycles_completed']} · Last: {DAEMON_STATE['last_run']}
        <form method="POST" action="/run-now" style="display:inline;margin-left:0.6rem;">
          <button type="submit" class="btn btn-secondary" style="padding:0.25rem 0.6rem;">🔄 Scan Now</button>
        </form>
      </div>
    </div>

    {flash_banner}

    <div style="margin-bottom:1.5rem;display:flex;gap:0.75rem;align-items:center;">
      <form method="POST" action="/audit-custom" style="display:flex;gap:0.5rem;flex:1;">
        <input type="url" name="url" placeholder="https://any-startup-to-audit.com" required style="flex:1;padding:0.6rem;border-radius:6px;border:1px solid #2e3346;background:#12141d;color:#fff;" />
        <input type="text" name="handle" placeholder="@founder_handle" style="width:180px;padding:0.6rem;border-radius:6px;border:1px solid #2e3346;background:#12141d;color:#fff;" />
        <button type="submit" class="btn btn-green">+ Audit Custom URL &amp; Extract Email</button>
      </form>
      <a class="btn btn-secondary" href="/micro-tool" target="_blank">🧲 Public Inbound Lead Magnet (/micro-tool) ↗</a>
    </div>

    <h2 style="font-size:1.2rem;margin-bottom:0.75rem;">1. Outbound "Work-Already-Done" Queue ({len(campaigns)} Harvested Startups)</h2>
    {rows_html}

    <h2 style="font-size:1.2rem;margin:2rem 0 0.75rem;">2. Active $2,500/mo Client Retention Deliveries (Monday Morning ROI Reports)</h2>
    {deliveries_html or '<p style="color:#9ca3af;font-size:0.9rem;">Click "💰 Generate $2.5k/mo Monday ROI Report" on any lead above to generate the weekly client retention report.</p>'}
  </div>
</body>
</html>
"""


def check_admin_auth(headers: Any) -> bool:
    """Enforces HTTP Basic Auth (`admin:<ADMIN_PASSWORD>`) or Bearer Token when `ADMIN_PASSWORD` is set."""
    admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not admin_password:
        return True  # Local dev mode when ADMIN_PASSWORD is not set

    auth_header = headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split("Bearer ", 1)[1].strip()
        return hmac.compare_digest(token, admin_password)

    if auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header.split("Basic ", 1)[1]).decode("utf-8")
            _, pwd = decoded.split(":", 1)
            return hmac.compare_digest(pwd, admin_password)
        except Exception:
            return False

    return False


def make_handler(config: Dict[str, Any]):
    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _require_admin(self) -> bool:
            if check_admin_auth(self.headers):
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="AegisSaS Operator Command Center"')
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"401 Unauthorized - Enter ADMIN_PASSWORD")
            return False

        def _send_html(self, content: str, status: int = 200) -> None:
            encoded = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
            encoded = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)
            flash = qs.get("flash", [""])[0]

            # PUBLIC ROUTE 1: Railway Healthcheck
            if path == "/healthz":
                self._send_json(
                    {
                        "status": "ok",
                        "daemon_running": DAEMON_STATE["running"],
                        "cycles_completed": DAEMON_STATE["cycles_completed"],
                        "last_run": DAEMON_STATE["last_run"],
                    }
                )
                return

            # PUBLIC ROUTE 2: Free Satellite Micro-Tool Lead Magnet
            if path == "/micro-tool":
                p = OUTPUT_DIR / "micro_tool_scanner.html"
                if not p.exists():
                    generate_satellite_micro_tool(config, OUTPUT_DIR)
                self._send_html(p.read_text(encoding="utf-8"))
                return

            # PUBLIC ROUTE 3: Prospect Crime Scene Report
            if path.startswith("/report/"):
                fname = Path(path.replace("/report/", "")).name
                p = OUTPUT_DIR / "reports" / fname
                if p.exists():
                    self._send_html(p.read_text(encoding="utf-8"))
                else:
                    self._send_html("<h1>Report not found</h1>", 404)
                return

            # PUBLIC ROUTE 4: Client Monday ROI Delivery Report
            if path.startswith("/delivery-report/"):
                fname = Path(path.replace("/delivery-report/", "")).name
                p = DELIVERIES_DIR / fname
                if p.exists():
                    self._send_html(p.read_text(encoding="utf-8"))
                else:
                    self._send_html("<h1>Delivery report not found</h1>", 404)
                return

            # ALL OTHER GET ROUTES REQUIRE ADMIN AUTH
            if path == "/api/inbound-leads":
                if not self._require_admin():
                    return
                with get_connection(DEFAULT_DB_PATH) as conn:
                    rows = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT * FROM prospects ORDER BY id DESC LIMIT 100"
                        ).fetchall()
                    ]
                self._send_json({"ok": True, "prospects": rows})
                return

            # ADMIN ROUTE: Operator Command Center
            if path == "/" or path == "/index.html":
                if not self._require_admin():
                    return
                self._send_html(render_dashboard_html(config, flash))
                return

            self._send_html("<h1>404 Not Found</h1>", 404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else ""

            # PUBLIC WEBHOOK: Amazon SES Email Tracker Events (OPENED / CLICKED / REPLIED / BOUNCED)
            if self.path == "/api/webhooks/ses-events":
                try:
                    payload = json.loads(raw_body) if raw_body.strip() else {}
                    res = record_ses_tracker_webhook(payload, DEFAULT_DB_PATH)
                    self._send_json(res, 200)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
                return

            # PUBLIC INBOUND FORM: Founder submits URL + Email on `/micro-tool`
            if self.path == "/api/inbound-scan":
                form = urllib.parse.parse_qs(raw_body)
                url = form.get("url", [""])[0].strip()
                email_in = form.get("email", [""])[0].strip()
                if not url:
                    self._send_html("<h1>Missing URL</h1>", 400)
                    return
                host = urllib.parse.urlparse(url).netloc.replace("www.", "") or url
                company = host.split(".")[0].capitalize()
                harvest_signals(
                    DEFAULT_DB_PATH,
                    custom_items=[
                        {
                            "channel": "inbound_micro_tool",
                            "source_url": f"{url}#inbound-{utc_now_iso()}",
                            "title": f"{company} Inbound Micro-Tool Scan",
                            "target_app_url": url,
                            "author_handle": email_in or "@inbound_founder",
                            "content": "Inbound scan",
                        }
                    ],
                )
                with get_connection(DEFAULT_DB_PATH) as conn:
                    row = conn.execute("SELECT id, slug FROM prospects WHERE app_url = ?", (url,)).fetchone()
                if row:
                    audit_prospect(row["id"], DEFAULT_DB_PATH)
                    if email_in:
                        with get_connection(DEFAULT_DB_PATH) as conn:
                            conn.execute("UPDATE prospects SET contact_email = ? WHERE id = ?", (email_in, row["id"]))
                    build_campaign_for_prospect(row["id"], config, DEFAULT_DB_PATH, OUTPUT_DIR)
                    self.send_response(303)
                    self.send_header("Location", f"/report/{row['slug']}-audit.html")
                    self.end_headers()
                    return

            # ALL OTHER POST ROUTES REQUIRE ADMIN AUTH
            if not self._require_admin():
                return

            # ADMIN API: Receive locally generated Antigravity report + patch bundle
            if self.path == "/api/sync-report":
                try:
                    data = json.loads(raw_body)
                    slug = Path(data["slug"]).name
                    report_html = data["report_html"]
                    reports_dir = OUTPUT_DIR / "reports"
                    reports_dir.mkdir(parents=True, exist_ok=True)
                    target_file = reports_dir / f"{slug}-audit.html"
                    target_file.write_text(report_html, encoding="utf-8")
                    self._send_json(
                        {
                            "ok": True,
                            "published_path": f"/report/{slug}-audit.html",
                        }
                    )
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 400)
                return

            form = urllib.parse.parse_qs(raw_body)
            msg = ""
            if self.path == "/run-now":
                res = execute_full_cycle(config, DEFAULT_DB_PATH)
                msg = f"Completed scan cycle: {res['scanned']} launches checked, {res['audited']} audited."
            elif self.path.startswith("/approve/"):
                cid = int(self.path.split("/approve/")[1])
                res = dispatch_campaign(cid, config, DEFAULT_DB_PATH)
                msg = f"Campaign #{cid} dispatched to {res['to_email']} -> {res['transport']}!"
            elif self.path.startswith("/deliver/"):
                pid = int(self.path.split("/deliver/")[1])
                res = generate_monday_roi_report(pid, "CompetitorApp", DEFAULT_DB_PATH)
                msg = f"Generated Monday Morning ROI Report for {res['company_name']} ({res['poached_leads_count']} competitor leads poached)!"
            elif self.path == "/audit-custom":
                url = form.get("url", [""])[0].strip()
                handle = form.get("handle", ["@founder"])[0].strip() or "@founder"
                if url:
                    host = urllib.parse.urlparse(url).netloc.replace("www.", "") or url
                    company = host.split(".")[0].capitalize()
                    harvest_signals(
                        DEFAULT_DB_PATH,
                        custom_items=[
                            {
                                "channel": "direct",
                                "source_url": url,
                                "title": f"{company} Custom Scan",
                                "target_app_url": url,
                                "author_handle": handle,
                                "content": "Custom scan",
                            }
                        ],
                    )
                    with get_connection(DEFAULT_DB_PATH) as conn:
                        row = conn.execute("SELECT id FROM prospects WHERE app_url = ?", (url,)).fetchone()
                    if row:
                        audit_prospect(row["id"], DEFAULT_DB_PATH)
                        build_campaign_for_prospect(row["id"], config, DEFAULT_DB_PATH, OUTPUT_DIR)
                        msg = f"Audited {company} ({url}) and queued Crime Scene report!"

            redirect_url = "/?" + urllib.parse.urlencode({"flash": msg})
            self.send_response(303)
            self.send_header("Location", redirect_url)
            self.end_headers()

    return DashboardHandler


def start_daemon_and_dashboard(config: Dict[str, Any]) -> None:
    daemon_cfg = config.get("daemon", {})
    interval_sec = int(os.environ.get("CRON_INTERVAL_SECONDS") or daemon_cfg.get("interval_seconds", 900))
    host = os.environ.get("HOST") or daemon_cfg.get("dashboard_host", "0.0.0.0")
    port = int(os.environ.get("PORT") or daemon_cfg.get("dashboard_port", 8080))

    worker = threading.Thread(target=background_cron_worker, args=(config, interval_sec), daemon=True)
    worker.start()

    server = ThreadingHTTPServer((host, port), make_handler(config))
    print("==========================================================================")
    print(" [ACTIVE] AegisSaS 15-Minute Autonomous Daemon & Web Dashboard RUNNING")
    print(f" [BIND]   Listening on http://{host}:{port} (Healthcheck: /healthz)")
    print(f" [CRON]   Auto-Harvest & Audit Interval: Every {interval_sec // 60} minutes ({interval_sec}s)")
    print("==========================================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down daemon...")
        server.server_close()
