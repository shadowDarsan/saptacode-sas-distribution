import base64
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from db.schema import get_connection, init_db
from modules.auditor import audit_prospect
from modules.daemon_dashboard import check_admin_auth
from modules.delivery_engine import generate_monday_roi_report
from modules.dispatcher import (
    dispatch_campaign,
    extract_contact_info,
    record_ses_tracker_webhook,
)
from modules.radar import harvest_signals, parse_agent_reach_envelope
from modules.wedge_generator import (
    build_campaign_for_prospect,
    generate_satellite_micro_tool,
)


class TestSaSPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.tmp_dir.name)
        self.db_path = self.base_path / "test_state.sqlite"
        self.out_dir = self.base_path / "output"
        init_db(self.db_path)
        self.config = {
            "brand_name": "AegisSaS Test",
            "public_base_url": "https://audit.yourdomain.com",
            "core_offer": {
                "pilot_price_usd": 299,
                "pilot_days": 7,
                "retainer_price_usd": 2500,
                "checkout_url": "https://buy.stripe.com/test_pilot_299",
            },
            "email_dispatch": {
                "provider": "ses_webhook",
                "from_name": "Founder @ AegisSaS",
                "from_email": "alerts@yourdomain.com",
                "ses_webhook_url": "",
                "ses_webhook_api_key": "",
            },
        }

    def tearDown(self) -> None:
        os.environ.pop("ADMIN_PASSWORD", None)
        self.tmp_dir.cleanup()

    def test_agent_reach_envelope_and_rate_limit_fallback(self) -> None:
        valid_json = json.dumps(
            {
                "schema_version": "1.0",
                "ok": True,
                "channel": "reddit",
                "action": "search",
                "items": [{"title": "Need QA help", "url": "https://test-startup.io"}],
                "error": None,
            }
        )
        items = parse_agent_reach_envelope(valid_json)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://test-startup.io")

        rate_limited_json = json.dumps(
            {
                "schema_version": "1.0",
                "ok": False,
                "channel": "twitter",
                "error": {"code": "RATE_LIMITED", "message": "Too many requests"},
            }
        )
        self.assertEqual(parse_agent_reach_envelope(rate_limited_json), [])

    def test_contact_email_and_social_extraction(self) -> None:
        html_sample = '<a href="mailto:ceo@superapp.io">Email Us</a> <a href="https://x.com/superapp_hq">X</a>'
        info = extract_contact_info("https://superapp.io", html_sample, "@fallback")
        self.assertEqual(info["contact_email"], "ceo@superapp.io")
        self.assertEqual(info["social_url"], "https://x.com/superapp_hq")

    def test_ses_webhook_post_and_tracker_events(self) -> None:
        captured_requests = []

        class MockSESHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                captured_requests.append(
                    {
                        "auth": self.headers.get("Authorization"),
                        "idempotency": self.headers.get("Idempotency-Key"),
                        "body": body,
                    }
                )
                resp = json.dumps({"ok": True, "message_id": "ses-msg-998877"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

        mock_server = HTTPServer(("127.0.0.1", 0), MockSESHandler)
        port = mock_server.server_port
        t = threading.Thread(target=mock_server.handle_request, daemon=True)
        t.start()

        self.config["email_dispatch"]["ses_webhook_url"] = f"http://127.0.0.1:{port}/api/send"
        self.config["email_dispatch"]["ses_webhook_api_key"] = "secret-ses-key-123"

        sample = [
            {
                "channel": "hackernews",
                "source_url": "https://news.ycombinator.com/item?id=999",
                "title": "Show HN: PayFlow SaaS with Supabase",
                "target_app_url": "https://payflow-demo.local",
                "author_handle": "@payflow_dev",
                "content": "Show HN launch!",
            }
        ]
        harvest_signals(self.db_path, custom_items=sample)
        audit_prospect(1, self.db_path)
        camp = build_campaign_for_prospect(1, self.config, self.db_path, self.out_dir)
        cid = camp["campaign_id"]

        # Unapproved direct status change must fail SQLite CHECK constraint
        with self.assertRaises(sqlite3.IntegrityError):
            with get_connection(self.db_path) as conn:
                conn.execute("UPDATE campaigns SET status = 'SENT' WHERE id = ?", (cid,))

        # Approve & POST to mock Amazon SES API
        dispatched = dispatch_campaign(cid, self.config, self.db_path, self.out_dir / "outbox")
        mock_server.server_close()

        self.assertEqual(dispatched["status"], "SENT")
        self.assertEqual(dispatched["ses_message_id"], "ses-msg-998877")
        self.assertEqual(len(captured_requests), 1)
        self.assertEqual(captured_requests[0]["auth"], "Bearer secret-ses-key-123")
        self.assertEqual(captured_requests[0]["idempotency"], f"aegissas-campaign-{cid}")
        self.assertEqual(captured_requests[0]["body"]["to"], "founders@payflow-demo.local")
        self.assertIn("https://audit.yourdomain.com/report/payflow-demo-local-audit.html", captured_requests[0]["body"]["html"])

        # Test receiving OPENED / CLICKED event from Amazon SES email tracker
        tracker_res = record_ses_tracker_webhook(
            {"message_id": "ses-msg-998877", "event_type": "CLICKED"},
            self.db_path,
        )
        self.assertEqual(tracker_res["updated_tracker_status"], "CLICKED")

        # Stage 5: Generate Monday Morning Client ROI Report
        roi = generate_monday_roi_report(1, "StripeCompetitor", self.db_path, self.out_dir / "deliveries")
        self.assertEqual(roi["poached_leads_count"], 3)
        self.assertTrue(Path(roi["roi_report_path"]).exists())

    def test_admin_auth_protection(self) -> None:
        os.environ["ADMIN_PASSWORD"] = "railway-secret-99"
        self.assertFalse(check_admin_auth({}))
        valid_b64 = base64.b64encode(b"admin:railway-secret-99").decode("utf-8")
        self.assertTrue(check_admin_auth({"Authorization": f"Basic {valid_b64}"}))
        self.assertTrue(check_admin_auth({"Authorization": "Bearer railway-secret-99"}))


if __name__ == "__main__":
    unittest.main()
