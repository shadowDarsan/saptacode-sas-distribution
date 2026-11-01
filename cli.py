import argparse
import json
import shutil
from pathlib import Path
from urllib.parse import urlparse

from db.schema import DEFAULT_DB_PATH, get_connection, init_db
from modules.auditor import audit_prospect, is_obscura_cdp_alive
from modules.daemon_dashboard import start_daemon_and_dashboard
from modules.delivery_engine import generate_monday_roi_report
from modules.dispatcher import dispatch_campaign
from modules.radar import harvest_signals
from modules.wedge_generator import (
    OUTPUT_DIR,
    build_campaign_for_prospect,
    generate_satellite_micro_tool,
)

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def cmd_doctor() -> None:
    print("=== AegisSaS Factory · Environment Doctor ===")
    tools = {
        "agent-reach (Panniantong/Agent-Reach)": shutil.which("agent-reach") or "Fallback: Built-in HN/Launch Harvester active",
        "obscura CDP (h4ckf0r0day/obscura)": "CONNECTED (ws://127.0.0.1:9222)" if is_obscura_cdp_alive() else "Fallback: Built-in HTTP/Bundle Probe active",
        "rea-agents (morluto/rea)": shutil.which("rea") or "Built-in SHA-256 Evidence Ledger active",
        "mirofish (666ghj/MiroFish)": "Built-in 3-Persona OASIS Pitch Evaluator active",
    }
    for k, v in tools.items():
        print(f"  [OK] {k:<40} -> {v}")


def cmd_run_pipeline() -> None:
    config = load_config()
    init_db(DEFAULT_DB_PATH)

    print("\n[Stage 1/5] Running Pain & Launch Harvester (`radar.py`)...")
    stats = harvest_signals(DEFAULT_DB_PATH)
    print(f"  -> Scanned {stats['scanned']} launches | New signals: {stats['new_signals']} | New prospects: {stats['new_prospects']}")

    print("\n[Stage 2/5] Running Silent Pre-Audit & Email Extractor (`auditor.py` + `dispatcher.py`)...")
    with get_connection(DEFAULT_DB_PATH) as conn:
        prospects = conn.execute("SELECT id, company_name, app_url FROM prospects").fetchall()

    audited_ids = []
    for p in prospects:
        res = audit_prospect(p["id"], DEFAULT_DB_PATH)
        if res:
            audited_ids.append(p["id"])
            print(
                f"  -> Audited {p['company_name']:<12} | Email: {res['contact_email']:<28} | "
                f"Evidence: {res['evidence_id']} ({res['total_issues']} bugs)"
            )

    print("\n[Stage 3 & 4] Running MiroFish Simulator & Generating 'Crime Scene' Reports (`wedge_generator.py`)...")
    for pid in audited_ids:
        with get_connection(DEFAULT_DB_PATH) as conn:
            existing = conn.execute("SELECT id FROM campaigns WHERE prospect_id = ?", (pid,)).fetchone()
        if existing:
            continue
        camp = build_campaign_for_prospect(pid, config, DEFAULT_DB_PATH, OUTPUT_DIR)
        print(f"  -> Campaign #{camp['campaign_id']} [{camp['company_name']}] | MiroFish Score: {camp['mirofish_score']}/100")
        print(f"     Report HTML : {camp['report_html_path']}")

    micro_tool_path = generate_satellite_micro_tool(config, OUTPUT_DIR)
    print(f"\n[Satellite Spoke] Generated Free Inbound Micro-Tool Page: {micro_tool_path}")


def cmd_audit_url(url: str, handle: str) -> None:
    config = load_config()
    init_db(DEFAULT_DB_PATH)
    host = urlparse(url).netloc.replace("www.", "") or url
    company = host.split(".")[0].capitalize()

    custom_item = [
        {
            "channel": "direct",
            "source_url": url,
            "title": f"{company} Direct Target Audit",
            "target_app_url": url,
            "author_handle": handle,
            "content": "Direct target scan",
        }
    ]
    harvest_signals(DEFAULT_DB_PATH, custom_items=custom_item)
    with get_connection(DEFAULT_DB_PATH) as conn:
        row = conn.execute("SELECT id FROM prospects WHERE app_url = ?", (url,)).fetchone()
    pid = row["id"]
    audit_res = audit_prospect(pid, DEFAULT_DB_PATH)
    camp = build_campaign_for_prospect(pid, config, DEFAULT_DB_PATH, OUTPUT_DIR)

    print(f"\n=== Instant Target Audit Complete: {company} ===")
    print(f"  Founder Email: {audit_res['contact_email']}")
    print(f"  Social Link  : {audit_res['social_url']}")
    print(f"  Evidence ID  : {audit_res['evidence_id']} ({audit_res['total_issues']} issues)")
    print(f"  Report File  : {camp['report_html_path']}")
    print(f"  Ready Message:\n  {camp['outreach_message']}")


def cmd_queue() -> None:
    init_db(DEFAULT_DB_PATH)
    with get_connection(DEFAULT_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT c.id, p.company_name, p.founder_handle, p.contact_email, p.social_url,
                   c.mirofish_score, c.status, c.report_html_path, c.outreach_message
            FROM campaigns c
            JOIN prospects p ON p.id = c.prospect_id
            ORDER BY c.id DESC
            """
        ).fetchall()

    print(f"=== Outbound Approval Queue ({len(rows)} campaigns) ===")
    for r in rows:
        print(
            f"\nCampaign #{r['id']} | {r['company_name']} | Email: {r['contact_email']} | "
            f"X: {r['social_url']} | Status: {r['status']}"
        )
        print(f"  Report : {r['report_html_path']}")
        print(f"  Message: {r['outreach_message']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="AegisSaS — $10M Service-as-a-Software & Distribution Factory CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("start", help="Start the 15-Minute Autonomous Daemon + Web Command Center (http://127.0.0.1:8080)")
    sub.add_parser("doctor", help="Verify environment & tool bridges")
    sub.add_parser("run-pipeline", help="Execute one full harvest -> audit -> email extract -> wedge cycle")
    sub.add_parser("queue", help="List campaigns with extracted emails awaiting 1-click dispatch")

    url_p = sub.add_parser("audit-url", help="Audit any specific startup URL on demand & extract founder email")
    url_p.add_argument("url", type=str, help="Target app URL (e.g. https://their-startup.com)")
    url_p.add_argument("--handle", type=str, default="@founder", help="Founder handle or name")

    approve_p = sub.add_parser("approve", help="Approve and send/dispatch a queued campaign by ID")
    approve_p.add_argument("campaign_id", type=int, help="Campaign ID to approve & send")

    deliver_p = sub.add_parser("deliver", help="Generate the Monday Morning $2,500/mo Client ROI & Poached Leads Report")
    deliver_p.add_argument("prospect_id", type=int, help="Prospect/Client ID")
    deliver_p.add_argument("--competitor", type=str, default="CompetitorApp", help="Competitor name to poach leads from")

    sync_p = sub.add_parser("push-railway", help="Push locally generated Antigravity reports up to your live Railway instance")
    sync_p.add_argument("--remote", type=str, required=True, help="Your Railway base URL (e.g. https://audit.yourdomain.com)")
    sync_p.add_argument("--password", type=str, required=True, help="Your ADMIN_PASSWORD set on Railway")

    args = parser.parse_args()
    config = load_config()

    if args.command == "start":
        start_daemon_and_dashboard(config)
    elif args.command == "doctor":
        cmd_doctor()
    elif args.command == "run-pipeline":
        cmd_run_pipeline()
    elif args.command == "audit-url":
        cmd_audit_url(args.url, args.handle)
    elif args.command == "queue":
        cmd_queue()
    elif args.command == "approve":
        res = dispatch_campaign(args.campaign_id, config, DEFAULT_DB_PATH)
        print(f"Dispatched Campaign #{res['campaign_id']} -> {res['to_email']} ({res['transport']})")
        print(f"Outbox Receipt: {res['receipt_path']}")
        print(f"1-Click Mailto: {res['mailto_url'][:90]}...")
    elif args.command == "deliver":
        res = generate_monday_roi_report(args.prospect_id, args.competitor, DEFAULT_DB_PATH)
        print(f"Generated Monday Morning ROI Report for {res['company_name']} ({res['poached_leads_count']} competitor leads poached)")
        print(f"Report File: {res['roi_report_path']}")
    elif args.command == "push-railway":
        import urllib.request

        reports_dir = OUTPUT_DIR / "reports"
        count = 0
        for html_file in reports_dir.glob("*-audit.html"):
            slug = html_file.name.replace("-audit.html", "")
            payload = json.dumps({"slug": slug, "report_html": html_file.read_text(encoding="utf-8")}).encode("utf-8")
            req = urllib.request.Request(
                f"{args.remote.rstrip('/')}/api/sync-report",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {args.password}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                count += 1
                print(f"  -> Synced {html_file.name} to {args.remote}/report/{html_file.name} (HTTP {resp.getcode()})")
        print(f"Successfully pushed {count} Antigravity reports to Railway!")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
