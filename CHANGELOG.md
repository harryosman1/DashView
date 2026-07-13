# Changelog

## v3.0 (2026-07-13)
The decision-system release. DashView goes from dashboard to advisor.

### New tools
- **Go-Live Gate** (Live tab): species-aware pass/hold readiness check for
  moving wallets from paper to real money, with named reasons per criterion
  and a manual-review list for what can't be auto-scored.
- **Parameter Optimizer** (Live tab): per-wallet analysis (capital-weighted
  peak concurrency, skip anatomy, species-routed stop verdicts), portfolio
  daily-P&L correlation, and two-phase preview/confirm Apply for sizing
  recommendations (writes targets, logs config history, runs rebalancer).
- **Shadow Scanner** (Shadow tab): automated daily verdicts with harvester
  detection, grace periods, dormancy flags; push alerts on verdict changes only.
- **Self-update notifications**: daily check of local HEAD vs GitHub; push
  when new commits land (uses the update_available toggle).

### Live tab improvements
- Bet Size stat per wallet (reflects rebalancer runs on next poll)
- Freshness indicators: last decision / last copy, colored when silent
- Positions button on live wallet cards
- Positions panel is live-tier aware (was hardcoded to shadow log) and
  sources open positions from the bot's authoritative accounting
- Open-position counts show distinct markets (not scale-in entry rows)
- Closed copies show WIN/LOSS badges with per-trade P&L

### Notifications
- Toggles are now wired to the send path (off means off); resolution pushes
  split into win/loss so those toggles work independently
- Fixed VAPID claim bug that broke all pushes (403 BadJwtToken)

### Infrastructure
- Scanner/gate/optimizer results cached; on-demand refresh from the UI
- config_history.json tracks per-wallet policy changes (gate reads it;
  optimizer Apply appends automatically)
- wallet_species.json routes gate/optimizer criteria per trading style
