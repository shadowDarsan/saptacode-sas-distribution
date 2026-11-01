import email.message
import html
import json
import os
import re
import smtplib
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from db.schema import DEFAULT_DB_PATH, DEFAULT_OUTPUT_DIR, get_connection, get_public_base_url, utc_now_iso

OUTBOX_DIR = DEFAULT_OUTPUT_DIR / "outbox"

IGNORE_EMAIL_DOMAINS = {
    "sentry.io",
    "example.com",
    "domain.com",
    "email.com",
    "wixpress.com",
    "w3.org",
}


def extract_contact_info(url: str, html_body: str = "", founder_handle: str = "@founder") -> Dict[str, str]:
    """Extracts founder email and X/Twitter URL from page HTML, with smart domain fallback."""
    host = urlparse(url).netloc.replace("www.", "").split(":")[0]
    found_email: Optional[str] = None

    if html_body:
        mailto_matches = re.findall(r"mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", html_body)
        raw_matches = re.findall(r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", html_body)
        for candidate in mailto_matches + raw_matches:
            c_lower = candidate.lower()
            domain_part = c_lower.split("@")[-1]
            if domain_part in IGNORE_EMAIL_DOMAINS:
                continue
            if any(c_lower.endswith(ext) for ext in (".png", ".jpg", ".svg", ".webp", ".js", ".css")):
                continue
            found_email = c_lower
            break

    if not found_email:
        clean_host = host if "." in host else "startup.io"
        found_email = f"founders@{clean_host}"

    social_url = ""
    if html_body:
        x_match = re.search(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([a-zA-Z0-9_]{1,25})", html_body)
        if x_match and x_match.group(1).lower() not in ("intent", "share", "home", "explore"):
            social_url = f"https://x.com/{x_match.group(1)}"

    if not social_url:
        clean_handle = founder_handle.lstrip("@").strip() or "founder"
        social_url = f"https://x.com/{clean_handle}"

    return {
        "contact_email": found_email,
        "social_url": social_url,
    }


def build_mailto_link(to_email: str, subject: str, body: str) -> str:
    params = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
    return f"mailto:{to_email}?{params}"


def format_html_email(body_text: str, report_url: str, company_name: str) -> str:
    """Formats a clean, high-deliverability HTML email body for the Amazon SES API."""
    escaped_lines = "<br/><br/>".join(html.escape(line) for line in body_text.split("\n") if line.strip())
    return (
        f'<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;font-size:15px;'
        f'line-height:1.6;color:#111827;max-width:600px;">'
        f"<p>{escaped_lines}</p>"
        f'<p style="margin-top:18px;">'
        f'<a href="{html.escape(report_url)}" style="background:#10b981;color:#04130d;font-weight:700;'
        f'text-decoration:none;padding:10px 16px;border-radius:6px;display:inline-block;">'
        f"View {html.escape(company_name)} Diagnostic Trace &amp; Free Patch →</a></p>"
        f"</div>"
    )


def send_via_ses_webhook(
    webhook_url: str,
    api_key: str,
    payload_dict: Dict[str, Any],
    campaign_id: int,
) -> Dict[str, Any]:
    """POSTs the approved campaign email to the user's custom Amazon SES API service."""
    raw_bytes = json.dumps(payload_dict).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "AegisSaS-Dispatcher/2.0",
        "Idempotency-Key": f"aegissas-campaign-{campaign_id}",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key

    req = urllib.request.Request(webhook_url, data=raw_bytes, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=12) as resp:
        resp_body = resp.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(resp_body) if resp_body.strip() else {}
        except json.JSONDecodeError:
            parsed = {"raw": resp_body}
        return {
            "http_status": resp.getcode(),
            "message_id": parsed.get("message_id") or parsed.get("id") or f"ses-camp-{campaign_id}",
            "response": parsed,
        }


def dispatch_campaign(
    campaign_id: int,
    config: Dict[str, Any],
    db_path: Path = DEFAULT_DB_PATH,
    outbox_dir: Path = OUTBOX_DIR,
) -> Dict[str, Any]:
    """
    Human-in-the-Loop Approval Gate:
    1. Marks campaign `approved_by_human = 1`.
    2. If `SES_WEBHOOK_URL` is configured (in env or config.json), sends HTTP POST to user's Amazon SES microservice.
    3. Saves audit receipt to `output/outbox/`.
    """
    outbox_dir.mkdir(parents=True, exist_ok=True)
    now = utc_now_iso()

    email_cfg = config.get("email_dispatch") or config.get("smtp") or {}
    ses_webhook_url = os.environ.get("SES_WEBHOOK_URL") or email_cfg.get("ses_webhook_url", "")
    ses_api_key = os.environ.get("SES_WEBHOOK_API_KEY") or email_cfg.get("ses_webhook_api_key", "")
    from_name = os.environ.get("FROM_NAME") or email_cfg.get("from_name", "Founder @ AegisSaS")
    from_email = os.environ.get("FROM_EMAIL") or email_cfg.get("from_email", "alerts@yourdomain.com")
    public_base = get_public_base_url(config)

    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT c.*, p.company_name, p.slug, p.contact_email, p.social_url, p.founder_handle, p.app_url, a.evidence_id
            FROM campaigns c
            JOIN prospects p ON p.id = c.prospect_id
            JOIN audits a ON a.id = c.audit_id
            WHERE c.id = ?
            """,
            (campaign_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Campaign #{campaign_id} not found")

        to_email = row["contact_email"] or f"founders@{urlparse(row['app_url']).netloc}"
        subject = row["outreach_subject"] or f"Quick bug flag on {row['company_name']} mobile flow"
        body_text = row["outreach_message"]
        live_report_url = f"{public_base}/report/{row['slug']}-audit.html"
        body_html = format_html_email(body_text, live_report_url, row["company_name"])

        ses_payload = {
            "event": "campaign.approved",
            "to": to_email,
            "from_name": from_name,
            "from_email": from_email,
            "subject": subject,
            "text": body_text,
            "html": body_html,
            "metadata": {
                "campaign_id": campaign_id,
                "prospect_id": row["prospect_id"],
                "company_name": row["company_name"],
                "app_url": row["app_url"],
                "founder_handle": row["founder_handle"],
                "evidence_id": row["evidence_id"],
                "report_url": live_report_url,
                "approved_at": now,
            },
        }

        ses_msg_id = f"local-{campaign_id}"
        tracker_status = "SENT_OUTBOX"

        if ses_webhook_url:
            ses_res = send_via_ses_webhook(ses_webhook_url, ses_api_key, ses_payload, campaign_id)
            ses_msg_id = str(ses_res["message_id"])
            tracker_status = "SENT_SES_API"
            delivery_note = f"POST_SES_WEBHOOK_OK (HTTP {ses_res['http_status']} · ID: {ses_msg_id})"
        else:
            delivery_note = "QUEUED_OUTBOX (Set SES_WEBHOOK_URL env var to POST to your Amazon SES API)"

        receipt_path = outbox_dir / f"campaign-{campaign_id}-{row['slug']}.json"
        receipt_path.write_text(
            json.dumps(
                {
                    "transport": delivery_note,
                    "ses_message_id": ses_msg_id,
                    "payload_sent": ses_payload,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        conn.execute(
            """
            UPDATE campaigns
            SET approved_by_human = 1,
                human_approver_timestamp = ?,
                ses_message_id = ?,
                tracker_status = ?,
                status = 'SENT'
            WHERE id = ?
            """,
            (now, ses_msg_id, tracker_status, campaign_id),
        )
        conn.execute("UPDATE prospects SET status = 'CONTACTED' WHERE id = ?", (row["prospect_id"],))

        return {
            "campaign_id": campaign_id,
            "to_email": to_email,
            "social_url": row["social_url"],
            "subject": subject,
            "body": body_text,
            "mailto_url": build_mailto_link(to_email, subject, body_text),
            "receipt_path": str(receipt_path),
            "ses_message_id": ses_msg_id,
            "transport": delivery_note,
            "status": "SENT",
        }


def record_ses_tracker_webhook(event_payload: Dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """
    Receives open/click/bounce/reply tracking webhooks from your Amazon SES service:
    POST /api/webhooks/ses-events
    Payload: {"campaign_id": 1, "event_type": "OPENED" | "CLICKED" | "REPLIED" | "BOUNCED"}
    """
    campaign_id = event_payload.get("campaign_id")
    ses_message_id = event_payload.get("message_id") or event_payload.get("ses_message_id")
    event_type = str(event_payload.get("event_type") or event_payload.get("event") or "OPENED").upper()

    with get_connection(db_path) as conn:
        if campaign_id:
            conn.execute(
                "UPDATE campaigns SET tracker_status = ? WHERE id = ?",
                (event_type, int(campaign_id)),
            )
        elif ses_message_id:
            conn.execute(
                "UPDATE campaigns SET tracker_status = ? WHERE ses_message_id = ?",
                (event_type, str(ses_message_id)),
            )
        else:
            raise ValueError("Webhook payload must include `campaign_id` or `message_id`")

    return {"ok": True, "updated_tracker_status": event_type}
