import html
import json
import os
from pathlib import Path
from typing import Any, Dict

from db.schema import DEFAULT_DB_PATH, DEFAULT_OUTPUT_DIR, get_connection, get_public_base_url, utc_now_iso
from modules.dispatcher import dispatch_campaign
from modules.simulator import run_mirofish_simulation

OUTPUT_DIR = DEFAULT_OUTPUT_DIR


def render_crime_scene_html(
    prospect: Dict[str, Any],
    audit: Dict[str, Any],
    config: Dict[str, Any],
) -> str:
    company = html.escape(prospect["company_name"])
    app_url = html.escape(prospect["app_url"])
    ev_id = html.escape(audit["evidence_id"])
    sha256 = html.escape(audit["sha256_digest"])
    engine = html.escape(audit["engine_used"])

    unlocked = audit["unlocked_issue"]
    locked_list = audit["locked_issues"]

    offer = config.get("core_offer", {})
    pilot_price = offer.get("pilot_price_usd", 299)
    retainer_price = offer.get("retainer_price_usd", 2500)
    checkout_url = html.escape(
        os.environ.get("STRIPE_CHECKOUT_URL") or offer.get("checkout_url", "https://buy.stripe.com/test_pilot_299")
    )

    locked_cards_html = ""
    for idx, item in enumerate(locked_list, start=2):
        sev = html.escape(item.get("severity", "HIGH"))
        badge_bg = "#7f1d1d" if sev == "CRITICAL" else "#78350f"
        badge_fg = "#fca5a5" if sev == "CRITICAL" else "#fcd34d"
        locked_cards_html += f"""
        <div class="card locked-card">
          <div class="card-header">
            <span class="badge" style="background:{badge_bg};color:{badge_fg};">🔒 ISSUE #{idx} · {sev}</span>
            <span class="mono-tag">{html.escape(item.get("id", "SEC-XX"))}</span>
          </div>
          <h3>{html.escape(item.get("title", ""))}</h3>
          <p class="muted">Impact: {html.escape(item.get("impact", ""))}</p>
          <div class="blurred-code">
            <code>// [LOCKED REMEDIATION PATCH &amp; PLAYWRIGHT E2E TEST SUITE — UNLOCK IN 7-DAY PILOT]</code>
          </div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Diagnostic Evidence Report · {company}</title>
  <style>
    :root {{
      --bg: #090a0f;
      --panel: #12141d;
      --border: #232736;
      --text: #f3f4f6;
      --muted: #9ca3af;
      --accent: #10b981;
      --accent-blue: #3b82f6;
      --danger: #ef4444;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
      line-height: 1.6;
      padding: 2rem 1rem 5rem;
    }}
    .container {{ max-width: 860px; margin: 0 auto; }}
    .topbar {{
      display: flex; justify-content: space-between; align-items: center;
      border-bottom: 1px solid var(--border); padding-bottom: 1rem; margin-bottom: 2rem;
      flex-wrap: wrap; gap: 0.75rem;
    }}
    .brand {{ font-weight: 700; font-size: 0.95rem; letter-spacing: 0.04em; color: var(--accent); }}
    .ev-pill {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.75rem; background: #181b26; border: 1px solid var(--border);
      padding: 0.3rem 0.65rem; border-radius: 6px; color: var(--muted);
    }}
    h1 {{ font-size: 1.85rem; margin-bottom: 0.5rem; }}
    .subtitle {{ color: var(--muted); margin-bottom: 1.75rem; font-size: 1rem; }}
    .meta-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 0.75rem; margin-bottom: 2rem;
    }}
    .meta-box {{
      background: var(--panel); border: 1px solid var(--border);
      padding: 0.85rem 1rem; border-radius: 8px;
    }}
    .meta-label {{ font-size: 0.72rem; text-transform: uppercase; color: var(--muted); letter-spacing: 0.05em; }}
    .meta-val {{ font-family: ui-monospace, monospace; font-size: 0.85rem; margin-top: 0.25rem; word-break: break-all; }}
    .card {{
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 10px; padding: 1.5rem; margin-bottom: 1.25rem;
    }}
    .unlocked-card {{ border-color: var(--accent); }}
    .locked-card {{ opacity: 0.92; border-style: dashed; }}
    .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }}
    .badge {{
      font-size: 0.72rem; font-weight: 700; padding: 0.25rem 0.6rem;
      border-radius: 4px; letter-spacing: 0.04em;
    }}
    .badge-free {{ background: #064e3b; color: #6ee7b7; }}
    .mono-tag {{ font-family: ui-monospace, monospace; font-size: 0.8rem; color: var(--muted); }}
    h3 {{ font-size: 1.15rem; margin-bottom: 0.5rem; }}
    .muted {{ color: var(--muted); font-size: 0.92rem; margin-bottom: 0.85rem; }}
    pre {{
      background: #06070a; border: 1px solid var(--border); border-radius: 8px;
      padding: 1rem; overflow-x: auto; font-family: ui-monospace, monospace;
      font-size: 0.83rem; color: #a7f3d0; margin-top: 0.75rem;
    }}
    .blurred-code {{
      background: #06070a; border: 1px solid var(--border); border-radius: 6px;
      padding: 0.75rem 1rem; font-family: ui-monospace, monospace;
      font-size: 0.8rem; color: #6b7280;
    }}
    .cta-panel {{
      background: linear-gradient(145deg, #131829, #0d1322);
      border: 1px solid var(--accent-blue); border-radius: 12px;
      padding: 2rem; margin-top: 2.25rem; text-align: left;
    }}
    .cta-panel h2 {{ font-size: 1.4rem; margin-bottom: 0.6rem; }}
    .cta-list {{ margin: 1rem 0 1.5rem 1.2rem; color: #d1d5db; font-size: 0.95rem; }}
    .cta-list li {{ margin-bottom: 0.45rem; }}
    .btn-primary {{
      display: inline-block; background: var(--accent); color: #04130d;
      font-weight: 700; text-decoration: none; padding: 0.9rem 1.6rem;
      border-radius: 8px; font-size: 1rem; transition: transform 0.15s ease;
    }}
    .btn-primary:hover {{ transform: translateY(-1px); }}
    .price-sub {{ display: block; margin-top: 0.6rem; font-size: 0.82rem; color: var(--muted); }}
  </style>
</head>
<body>
  <div class="container">
    <div class="topbar">
      <div class="brand">⚡ {html.escape(config.get("brand_name", "AegisSaS"))} · Autonomous Diagnostic</div>
      <div class="ev-pill">REA Evidence ID: {ev_id}</div>
    </div>

    <h1>Live Production Audit: {company}</h1>
    <p class="subtitle">
      Our headless browser &amp; static bundle engine (<code style="color:#93c5fd">{engine}</code>) inspected
      <a href="{app_url}" style="color:#60a5fa">{app_url}</a> and recorded <strong>{audit["total_issues"]} production issues</strong>.
      Issue #1 and its patch are unlocked below for free.
    </p>

    <div class="meta-grid">
      <div class="meta-box">
        <div class="meta-label">Target Endpoint</div>
        <div class="meta-val">{app_url}</div>
      </div>
      <div class="meta-box">
        <div class="meta-label">Evidence SHA-256 Digest</div>
        <div class="meta-val">{sha256[:24]}...</div>
      </div>
      <div class="meta-box">
        <div class="meta-label">Detected Risks</div>
        <div class="meta-val" style="color:#f87171">{audit["total_issues"]} Verified Issues</div>
      </div>
    </div>

    <!-- UNLOCKED WORK-ALREADY-DONE CARD -->
    <div class="card unlocked-card">
      <div class="card-header">
        <span class="badge badge-free">✅ ISSUE #1 UNLOCKED (FREE FIX INCLUDED) · {html.escape(unlocked["severity"])}</span>
        <span class="mono-tag">{html.escape(unlocked["id"])}</span>
      </div>
      <h3>{html.escape(unlocked["title"])}</h3>
      <p class="muted"><strong>Observed Trace:</strong> {html.escape(unlocked["trace"])}</p>
      <p class="muted"><strong>Business Impact:</strong> {html.escape(unlocked["impact"])}</p>
      <div class="meta-label" style="margin-top:0.5rem;">Copy-Paste Remediation Patch:</div>
      <pre><code>{html.escape(unlocked["fix_snippet"])}</code></pre>
    </div>

    <!-- LOCKED FINDINGS -->
    {locked_cards_html}

    <!-- SERVICE-AS-A-SOFTWARE UPSELL -->
    <div class="cta-panel">
      <h2>Stop Shipping Regressions &amp; Security Leaks on Every Deploy</h2>
      <p class="muted">
        Instead of hiring a $12,000/mo QA &amp; Security engineer, plug our <strong>Autonomous Hardening Engine</strong>
        into your repository.
      </p>
      <ul class="cta-list">
        <li><strong>Instant Unlock:</strong> Get the exact remediation patches &amp; Playwright tests for Issues #2–#{audit["total_issues"]} above today.</li>
        <li><strong>24/7 Headless E2E Verification:</strong> Every deploy is tested on Desktop &amp; Mobile Safari via Obscura before customers hit a broken button.</li>
        <li><strong>Done-For-You Fix PRs &amp; Weekly Competitor Leads:</strong> We open surgical Pull Requests fixing bugs and send you warm leads complaining about your competitors every Monday.</li>
      </ul>
      <a class="btn-primary" href="{checkout_url}">
        Unlock All {audit["total_issues"]} Fixes + Start {offer.get("pilot_days", 7)}-Day Pilot (${pilot_price}) →
      </a>
      <span class="price-sub">
        Then ${retainer_price:,}/mo for continuous 24/7 autonomous QA, security auditing &amp; weekly competitor lead drops. Cancel anytime.
      </span>
    </div>
  </div>
</body>
</html>
"""


def generate_satellite_micro_tool(config: Dict[str, Any], output_dir: Path = OUTPUT_DIR) -> Path:
    """Generates the free Satellite Micro-Tool Landing Page with live POST to `/api/inbound-scan`."""
    output_dir.mkdir(parents=True, exist_ok=True)
    tool_path = output_dir / "micro_tool_scanner.html"
    brand = html.escape(config.get("brand_name", "AegisSaS"))

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Free 1-Click Supabase RLS &amp; Checkout Leak Scanner · {brand}</title>
  <style>
    body {{
      background: #090a0f; color: #f3f4f6;
      font-family: -apple-system, BlinkMacSystemFont, "Inter", sans-serif;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; margin: 0; padding: 1.5rem;
    }}
    .hero-box {{
      max-width: 640px; width: 100%; background: #12141d;
      border: 1px solid #232736; border-radius: 14px; padding: 2.5rem;
      text-align: center; box-shadow: 0 20px 50px rgba(0,0,0,0.5);
    }}
    .pill {{
      display: inline-block; background: #064e3b; color: #6ee7b7;
      font-size: 0.75rem; font-weight: 700; padding: 0.3rem 0.75rem;
      border-radius: 999px; margin-bottom: 1rem;
    }}
    h1 {{ font-size: 2rem; line-height: 1.25; margin-bottom: 0.75rem; }}
    p {{ color: #9ca3af; font-size: 1rem; margin-bottom: 1.75rem; }}
    .input-group {{ display: flex; flex-direction: column; gap: 0.75rem; }}
    input {{
      padding: 0.9rem 1rem; border-radius: 8px; border: 1px solid #2e3346;
      background: #090a0f; color: #fff; font-size: 0.95rem;
    }}
    button {{
      padding: 0.95rem; border-radius: 8px; border: none;
      background: #10b981; color: #04130d; font-weight: 700;
      font-size: 1rem; cursor: pointer;
    }}
    .stats {{
      display: flex; justify-content: space-around; margin-top: 2rem;
      padding-top: 1.25rem; border-top: 1px solid #232736;
      font-size: 0.82rem; color: #9ca3af;
    }}
  </style>
</head>
<body>
  <div class="hero-box">
    <span class="pill">FREE SATELLITE DIAGNOSTIC TOOL · ZERO SIGNUP</span>
    <h1>Did Bolt, Lovable, or Cursor Leave Your App Exposed?</h1>
    <p>
      Paste your live URL below. Our Rust headless browser (<strong>Obscura</strong>) and bundle inspector
      (<strong>REA</strong>) will test your mobile signup flow, Supabase RLS policies, and security headers in 15 seconds.
    </p>
    <form class="input-group" method="POST" action="/api/inbound-scan">
      <input type="url" name="url" placeholder="https://your-startup.com" required />
      <input type="email" name="email" placeholder="founder@your-startup.com (to unlock your free patch)" required />
      <button type="submit">Run Free 15-Second Security &amp; Checkout Audit →</button>
    </form>
    <div class="stats">
      <span>⚡ 30MB Headless Rust Engine</span>
      <span>🔒 Cryptographic SHA-256 Proof</span>
      <span>🛠️ Includes Free Code Patch</span>
    </div>
  </div>
</body>
</html>
"""
    tool_path.write_text(html_content, encoding="utf-8")
    return tool_path


def build_campaign_for_prospect(
    prospect_id: int,
    config: Dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
    output_dir: Path = OUTPUT_DIR,
) -> Dict[str, Any]:
    """Generates the Crime Scene HTML report, runs MiroFish pitch simulation, and queues in `PENDING_APPROVAL`."""
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    public_base = get_public_base_url(config)

    with get_connection(db_path) as conn:
        prospect_row = conn.execute("SELECT * FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
        audit_row = conn.execute("SELECT * FROM audits WHERE prospect_id = ?", (prospect_id,)).fetchone()
        if not prospect_row or not audit_row:
            raise ValueError(f"Prospect {prospect_id} must be audited before building a campaign.")

        prospect = dict(prospect_row)
        audit = {
            "id": audit_row["id"],
            "evidence_id": audit_row["evidence_id"],
            "sha256_digest": audit_row["sha256_digest"],
            "engine_used": audit_row["engine_used"],
            "total_issues": audit_row["total_issues"],
            "unlocked_issue": json.loads(audit_row["unlocked_issue_json"]),
            "locked_issues": json.loads(audit_row["locked_issues_json"]),
        }

        report_filename = f"{prospect['slug']}-audit.html"
        report_path = reports_dir / report_filename
        html_doc = render_crime_scene_html(prospect, audit, config)
        report_path.write_text(html_doc, encoding="utf-8")

        public_report_url = f"{public_base}/report/{report_filename}"
        sim_result = run_mirofish_simulation(
            company_name=prospect["company_name"],
            founder_handle=prospect["founder_handle"],
            app_url=prospect["app_url"],
            unlocked_issue=audit["unlocked_issue"],
            report_url=public_report_url,
        )

        now = utc_now_iso()
        cur = conn.execute(
            """
            INSERT INTO campaigns
            (prospect_id, audit_id, report_html_path, outreach_message, mirofish_score, persona_feedback_json, approved_by_human, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 'PENDING_APPROVAL', ?)
            """,
            (
                prospect_id,
                audit["id"],
                str(report_path),
                sim_result["winning_message"],
                sim_result["mirofish_score"],
                json.dumps(sim_result["winning_reviews"]),
                now,
            ),
        )
        conn.execute("UPDATE prospects SET status = 'CAMPAIGN_QUEUED' WHERE id = ?", (prospect_id,))
        campaign_id = cur.lastrowid

    return {
        "campaign_id": campaign_id,
        "prospect_slug": prospect["slug"],
        "company_name": prospect["company_name"],
        "report_html_path": str(report_path),
        "outreach_message": sim_result["winning_message"],
        "mirofish_score": sim_result["mirofish_score"],
        "status": "PENDING_APPROVAL",
    }


def approve_and_dispatch_campaign(campaign_id: int, db_path: Path = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Backwards-compatible helper delegating to `dispatch_campaign`."""
    return dispatch_campaign(campaign_id, {}, db_path)
