# Engineering Verification & Test Plan (`sas-distribution-engine`)

## 1. Codepath-to-Test Coverage Diagram

```
  [Input / Trigger]              [Module / Codepath]                [Verification & Test Suite]
  ─────────────────              ───────────────────                ───────────────────────────
  1. Cron / CLI Run      ───►    modules/radar.py           ───►    test_radar_envelope.py
     (`agent-reach`)             • Parses JSON envelope             • Verifies `ok: true/false` handling
                                 • Deduplicates URLs in SQLite      • Simulates `RATE_LIMITED` & `NEED_LOGIN`
                                 • Scores buyer intent (0-100)      • Asserts zero duplicate prospect rows

  2. Qualified Prospect  ───►    modules/auditor.py         ───►    test_auditor_cdp.py
     URL                         • Connects Playwright to           • Verifies `ws://127.0.0.1:9222` handshake
                                   `obscura serve`                  • Tests 30s timeout kill-switch on hung pages
                                 • Runs `rea-agents` bundle check   • Verifies Evidence ID & SHA-256 integrity

  3. Candidate Hook /    ───►    modules/simulator.py       ───►    test_simulator_scoring.py
     Audit Summary               • Spawns 15/300 buyer personas     • Evaluates structured JSON sentiment output
                                 • Scores reply/click probability   • Enforces max token ceiling per run

  4. Approved Campaign   ───►    modules/wedge_generator.py ───►    test_delivery_safety.py
                                 • Renders static WAD report        • Verifies `approved_by_human == True` before
                                 • Queues outbound / social reply     any external webhook/SMTP dispatch
```

## 2. Failure Modes & Critical Gap Registry

| Failure Scenario | Severity | Test Coverage | Mitigation |
| :--- | :--- | :--- | :--- |
| Upstream social platform changes DOM (`agent-reach` fails) | High | `test_radar_envelope.py::test_upstream_broken_fallback` | Auto-runs `agent-reach doctor`, logs degraded channel, falls back to secondary search channel. |
| Malicious prospect webpage attempts infinite JS loop | High | `test_auditor_cdp.py::test_infinite_loop_timeout` | Hard 30-second execution deadline + process group termination on `obscura` worker. |
| LLM hallucinates a bug that does not exist on prospect's site | Critical | `test_auditor_cdp.py::test_evidence_hash_required` | Every reported bug MUST attach a real HTTP status code, console error, or `REA` evidence digest + screenshot. |
| Accidental unreviewed mass-outbound spam | Critical | `test_delivery_safety.py::test_unapproved_send_blocked` | Database constraint prevents transition to `SENT` without a non-null `human_approver_timestamp`. |
