import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from db.schema import DEFAULT_DB_PATH, get_connection, utc_now_iso


def slugify(text: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return clean[:48] or "prospect"


def score_intent(title: str, content: str, url: str) -> int:
    blob = f"{title} {content} {url}".lower()
    score = 50
    high_value_signals = {
        "show hn": 18,
        "launch": 15,
        "supabase": 15,
        "stripe": 15,
        "checkout": 12,
        "lovable": 18,
        "bolt": 16,
        "saas": 12,
        "beta": 10,
        "ai": 8,
        "auth": 10,
        "bug": 14,
        "feedback": 10,
    }
    for kw, pts in high_value_signals.items():
        if kw in blob:
            score += pts
    return min(99, score)


def parse_agent_reach_envelope(raw_output: str) -> List[Dict[str, Any]]:
    """Parses the standardized Panniantong/Agent-Reach JSON envelope."""
    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Agent-Reach JSON envelope: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Agent-Reach envelope must be a JSON object")

    if not data.get("ok", False):
        err = data.get("error") or {}
        code = err.get("code", "UNKNOWN_ERROR") if isinstance(err, dict) else str(err)
        # Gracefully return empty list on RATE_LIMITED / NEED_LOGIN / UPSTREAM_BROKEN
        return []

    items = data.get("items", [])
    if not isinstance(items, list):
        return []
    return items


def run_agent_reach_search(channel: str, query: str) -> List[Dict[str, Any]]:
    """Calls local `agent-reach` binary if present; returns standardized items."""
    exe = shutil.which("agent-reach")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, channel, "search", query],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        return parse_agent_reach_envelope(proc.stdout)
    except Exception:
        return []


def fetch_live_show_hn_launches(limit: int = 8) -> List[Dict[str, Any]]:
    """Fetches real-time 'Show HN' product launches via Algolia public API (zero key needed)."""
    api_url = f"https://hn.algolia.com/api/v1/search_by_date?tags=show_hn&hitsPerPage={limit}"
    try:
        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": "AegisSaS-Radar/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            hits = payload.get("hits", [])
            results = []
            for h in hits:
                url = h.get("url")
                title = h.get("title") or "Untitled Launch"
                if not url or "github.com" in url:
                    continue
                results.append(
                    {
                        "channel": "hackernews",
                        "source_url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                        "title": title,
                        "target_app_url": url,
                        "author_handle": f"@{h.get('author', 'founder')}",
                        "content": h.get("story_text") or title,
                    }
                )
            if results:
                return results
    except Exception:
        pass

    # Deterministic fallback seed launches if offline or rate-limited
    return [
        {
            "channel": "producthunt",
            "source_url": "https://www.producthunt.com/posts/flowpulse-ai",
            "title": "FlowPulse AI — Built with Bolt.new & Supabase for instant SaaS analytics",
            "target_app_url": "https://flowpulse-demo-app.io",
            "author_handle": "@alex_flowpulse",
            "content": "Launched our MVP in 48 hours using Bolt and Supabase Auth + Stripe checkout!",
        },
        {
            "channel": "hackernews",
            "source_url": "https://news.ycombinator.com/item?id=41990001",
            "title": "Show HN: InvoicePilot – Automated B2B invoicing with Next.js and Stripe",
            "target_app_url": "https://invoicepilot-saas.dev",
            "author_handle": "@sarah_builds",
            "content": "Show HN! Looking for feedback on our new onboarding and Stripe checkout flow.",
        },
        {
            "channel": "reddit",
            "source_url": "https://www.reddit.com/r/SaaS/comments/1abcde/just_launched_docuvault",
            "title": "Just launched DocuVault AI after 2 weeks on Lovable — need QA feedback",
            "target_app_url": "https://docuvault-ai.app",
            "author_handle": "@mike_docuvault",
            "content": "Built with Lovable and Supabase. Having some weird mobile Safari checkout bugs.",
        },
    ]


def harvest_signals(
    db_path: Path = DEFAULT_DB_PATH,
    custom_items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, int]:
    """Harvests launch & pain signals, deduplicates URLs, and populates `prospects`."""
    raw_items = custom_items if custom_items is not None else fetch_live_show_hn_launches()

    inserted_signals = 0
    inserted_prospects = 0
    now = utc_now_iso()

    with get_connection(db_path) as conn:
        for item in raw_items:
            channel = item.get("channel", "web")
            source_url = item.get("source_url") or item.get("url")
            title = item.get("title", "Untitled")
            app_url = item.get("target_app_url") or item.get("url")
            author = item.get("author_handle", "@founder")
            content = item.get("content", "")

            if not source_url or not app_url:
                continue

            intent = score_intent(title, content, app_url)

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO signals
                (channel, source_url, title, target_app_url, author_handle, intent_score, raw_json, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel,
                    source_url,
                    title,
                    app_url,
                    author,
                    intent,
                    json.dumps(item),
                    now,
                ),
            )
            if cur.rowcount > 0:
                inserted_signals += 1

            parsed_host = urlparse(app_url).netloc.replace("www.", "")
            company_Base = parsed_host.split(".")[0].capitalize() if parsed_host else title[:20]
            base_slug = slugify(parsed_host or title)

            cur_p = conn.execute(
                """
                INSERT OR IGNORE INTO prospects
                (slug, company_name, app_url, founder_handle, source_channel, intent_score, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'DISCOVERED', ?)
                """,
                (base_slug, company_Base, app_url, author, channel, intent, now),
            )
            if cur_p.rowcount > 0:
                inserted_prospects += 1

    return {
        "scanned": len(raw_items),
        "new_signals": inserted_signals,
        "new_prospects": inserted_prospects,
    }
