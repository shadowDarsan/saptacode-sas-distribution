# Deferred Scope & Future Expansion (`TODOS.md`)

1. **[Phase 2 Deferred] Self-Serve Multi-Tenant Billing Portal:**
   - *Rationale:* Do not build a custom Stripe billing dashboard before onboarding the first 5 paying Service-as-a-Software retainer clients ($2,500/mo). Use direct Stripe Payment Links until $15k MRR.
2. **[Phase 2 Deferred] Automated Video Loom Narration Generator:**
   - *Rationale:* Start with interactive HTML + annotated screenshot "Work-Already-Done" reports (`wedge_generator.py`). Add AI voiceover video synthesis on top of `Obscura` recordings once base conversion rate > 5%.
3. **[Phase 3 Deferred] Residential Proxy Pool Auto-Rotation for `Obscura`:**
   - *Rationale:* Built-in `--stealth` fingerprinting in `Obscura` handles initial volume (100-500 audits/day). Add proxy rotation only when scaling past 1,000 daily audits.
