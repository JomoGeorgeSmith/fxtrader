# FX analyst: candidate-driven implementation

This is an advisory, synthetic POC. It does not execute trades, edit source
workbooks, provide executable quotes or establish official JN/BOJ compliance.

## Modules and flow

- `backend/treasury_context.py` exposes a read-only canonical snapshot manifest:
  recorded holdings and holds, flows, status, source row, synthetic marker,
  market-reference quote status and **unreconciled** risk positions.
- `backend/opportunity_registry.py` registers extensible detection functions.
  They emit stable candidate IDs, evidence, materiality, dependencies and
  hypotheses, **not** safe or profitable trades. Additional analysts may use
  the context and registry protocol without inheriting FX advice.
- `backend/fx_investigation.py` preflights matched receipts, round trips,
  payment-linked order alternatives, three currencies for a cash gap, and a
  substantial buffer-limited exposure-reduction hypothesis using
  deterministic tools. All call arguments, results and failed alternatives
  appear in `analyst_audit.candidate_tool_calls`. Other candidates remain
  available to the single FX writer for investigation or documented deferral.
- `backend/portfolio_scenario.py` compares explicitly selected Planned orders
  against a set of cancellation/amendment, retention, trade and allocation
  operations on one dated ledger. It guards duplicate allocations, account
  debits, modeled settlement, receipt delay and cash buffers. Reported fee
  avoidance is indicative; principal retained is **not profit**. The separate
  Risk_Inputs position can only supply a provisional exposure change.
- `backend/portfolio_decisions.py` identifies shared-resource alternatives,
  evaluates combinations as a unit and allows at most one displayed modeled
  action in a decision group. Suppressed alternatives remain in the audit.
- `backend/analyst.py` uses one FX writer after candidate discovery; it no
  longer performs a CAD-only model assessment first. The writer can fetch the
  full snapshot or canonical manifest and call the scenario engine. Its strict
  output includes explicit candidate dispositions; missing dispositions
  trigger a bounded continuation request or an incomplete-run diagnostic.
  Published actions still need a passing calculation and cited evidence.
- `backend/app.py` preserves workbook/Kafka ingestion, run snapshotting,
  SQLite history, usage accounting and the event stream. The React overview
  displays candidate dispositions under Portfolio investigation, and the
  detail drawer states the approval, price and reconciliation conditions.

Existing `funding_plan`, `fx_scenarios` (including forward and reserve tools),
and the historical orchestrator remain callable. Only the *current live FX
writer path* skips pre-routed funding. No Investment or Liquidity Analyst
logic is implemented; those specialists can later reuse the normalized
snapshot, candidate/what-if interfaces, decision grouping and event pipeline.

## Verification (offline, no paid model calls)

From `FX Analyst/` run `./.venv/Scripts/python.exe -m unittest discover -s tests -q`.
From `frontend/` run `node --test tests/*.test.ts` and `npm.cmd run build`.
The integration test `tests/test_offline_pipeline.py` uses a stub author to
exercise discovery through publication; **it does not validate real model
discovery quality or an actual paid run**.

On the current synthetic workbooks, the offline scanner found **33** candidate
relationships (8 receipt/purchase, 1 opposing-order, 1 linked-order cash
alternative, 1 CAD gap, 1 USD exposure, 8 future obligations, 12 timing
mismatches and 1 rate review). Preflight made **23** deterministic tool
invocations, investigated **12** candidates, and left **21** for the FX
analyst to disposition. Tested comparisons include approximately JMD 76,030.75
of recorded GBP purchase fee avoidance; approximately GBP 4,551.52 principal
loss plus stated fees for the opposing orders (about JMD 933,328 indicative
cost avoidance); a passing modeled USD-to-CAD choice among USD/JMD/GBP; and
a separate modeled single-order cancellation versus paying from USD cash, and
a provisional USD exposure-reduction alternative. The risk/cash discrepancy
has not been reconciled, so the latter is **not** verified remediation.
These are synthetic, independent hypotheses, **not** validated joint
recommendations or guaranteed savings. The stub-author integration test
publishes five indicative cards; **no live author was run**.

## Known gaps and constraints

The actual AI's candidate coverage, priorities, tool selections and prose
remain unverified until an approved live run. There is no automatic resume
after a provider failure; the run records its incomplete status for manual
retry. Forward-versus-spot pricing is illustrative, not an executable dealer
quote or forecast. There is no reconciliation ledger linking risk positions
to operating cash, no proof that all after-holds balances are deployable,
and no verified bank settlement delivery. Calendar rules and permissions are
synthetic. A combination requires a shared-state scenario; separately
passing options must never be added to claim joint feasibility. Automatic
workbook change events still use the existing watcher and Kafka behavior;
configure/authorize automatic paid runs separately before editing watched
workbooks, demo rules or prompt files in a running API.
