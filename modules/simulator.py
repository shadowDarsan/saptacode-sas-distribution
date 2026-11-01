from typing import Any, Dict, List


PERSONA_ARCHETYPES: List[Dict[str, Any]] = [
    {
        "id": "persona_cynical_cto",
        "name": "Marcus (Cynical Staff Eng / CTO)",
        "bias": "Hates sales fluff and AI buzzwords; only clicks links that contain real technical stack traces or SHA-256 evidence.",
        "weight": 0.40,
    },
    {
        "id": "persona_launch_founder",
        "name": "Elena (Solo AI SaaS Founder on Launch Day)",
        "bias": "Terrified of broken signup/checkout flows costing launch traffic; loves free copy-paste fixes.",
        "weight": 0.35,
    },
    {
        "id": "persona_growth_ceo",
        "name": "David (Seed-Stage CEO)",
        "bias": "Cares about conversion rate and engineering velocity; willing to pay $299-$2,500/mo if someone takes QA off his plate.",
        "weight": 0.25,
    },
]


def generate_candidate_messages(
    company_name: str,
    founder_handle: str,
    app_url: str,
    unlocked_issue: Dict[str, str],
    report_url: str,
) -> List[Dict[str, str]]:
    issue_title = unlocked_issue["title"]
    issue_id = unlocked_issue["id"]

    return [
        {
            "angle": "work_already_done_note",
            "text": (
                f"Hey {founder_handle} — saw {company_name} ({app_url}) today. "
                f"While testing your flow on mobile viewport, I noticed `{issue_id}` ({issue_title}). "
                f"I captured the network trace and wrote the copy-paste patch for you here (no login needed): {report_url} "
                f"— no pitch, just wanted to flag it before it bites your live traffic!"
            ),
        },
        {
            "angle": "urgent_security_warning",
            "text": (
                f"URGENT for {company_name}: Our scanner found {issue_title} on {app_url}. "
                f"Click here to view your security report and buy our $2,500/mo remediation plan: {report_url}"
            ),
        },
        {
            "angle": "generic_agency_pitch",
            "text": (
                f"Hi {founder_handle}, congrats on {company_name}! We run an AI-powered QA and dev agency "
                f"helping startups scale faster for $2,500/mo. Do you have 15 mins for a discovery call this week?"
            ),
        },
    ]


def evaluate_message_with_swarm(candidate: Dict[str, str], unlocked_issue: Dict[str, str]) -> Dict[str, Any]:
    """Simulates MiroFish multi-persona evaluation on a candidate outreach message."""
    text = candidate["text"]
    angle = candidate["angle"]

    persona_reviews = []
    weighted_total = 0.0

    for p in PERSONA_ARCHETYPES:
        if angle == "work_already_done_note":
            score = 91.0 if p["id"] != "persona_cynical_cto" else 87.5
            reaction = (
                f"Names exact bug `{unlocked_issue['id']}`, includes zero-login fix link, and explicitly says 'no pitch'. "
                f"I would click the link immediately to check my prod site."
            )
        elif angle == "urgent_security_warning":
            score = 48.0
            reaction = "Sounds alarmist and pushy with the immediate $2,500/mo ask before proving value."
        else:
            score = 16.0
            reaction = "Instant archive/delete. Looks like every other cold spam email in my inbox."

        weighted_total += score * p["weight"]
        persona_reviews.append(
            {
                "persona": p["name"],
                "score": round(score, 1),
                "reaction": reaction,
            }
        )

    return {
        "angle": angle,
        "text": text,
        "composite_score": round(weighted_total, 2),
        "persona_reviews": persona_reviews,
    }


def run_mirofish_simulation(
    company_name: str,
    founder_handle: str,
    app_url: str,
    unlocked_issue: Dict[str, str],
    report_url: str,
) -> Dict[str, Any]:
    """Generates candidate hooks, runs multi-persona swarm scoring, and returns the winning hook."""
    candidates = generate_candidate_messages(
        company_name=company_name,
        founder_handle=founder_handle,
        app_url=app_url,
        unlocked_issue=unlocked_issue,
        report_url=report_url,
    )
    evaluated = [evaluate_message_with_swarm(c, unlocked_issue) for c in candidates]
    evaluated.sort(key=lambda x: x["composite_score"], reverse=True)
    winner = evaluated[0]

    return {
        "winning_angle": winner["angle"],
        "winning_message": winner["text"],
        "mirofish_score": winner["composite_score"],
        "winning_reviews": winner["persona_reviews"],
        "all_candidates": evaluated,
    }
