# FX Analyst — handover for the next coding assistant

**Read this file, `NEW_MACHINE_SETUP.md`, `README.md` and `docs/fx-simulation.md` before acting.** This is a working-context handover, not an export of the complete OpenChamber development conversation.

## First task on the new machine

Help the user install/check the dependencies in `NEW_MACHINE_SETUP.md`, restore this existing project, run offline verification, and start the application without a paid analyst run. Do not assume Docker, Python, Node.js or Git are installed. Ask for corporate IT/admin approval where required. Configure credentials privately; never request that the user paste a key into this conversation.

The user's instruction to the old assistant was: prepare a credential-free project transfer ZIP, preserve the files and working context, and include a document directing the new assistant to install dependencies such as Docker and Python. It was **not** permission for paid model testing, production banking integration or executing trades.

## Project and design

- JN Treasury FX analyst proof of concept: FastAPI/Python backend, React/TypeScript frontend, Kafka workbook-change events, SQLite audit history and immutable snapshots.
- Supported currencies: JMD, USD, GBP, CAD. Advisory only; a human approves and executes outside this application. There is no transaction execution.
- The bank is simulated through editable Excel workbooks in `shared/`. Existing edited data must be preserved, not regenerated from seed examples.
- Python validates workbook structure and calculation inputs and owns Decimal arithmetic, cash forecasts, scenarios and displayed action figures. The AI chooses investigations, currencies, amounts, dates, judgments and narrative.
- Keep the analyst model **gpt-4.1**. No model changes/comparisons or silent fallback. `FX_ANALYST_MODEL` is an existing environment override, not permission to change the agreed model.
- No fixed opportunity shortlist, forced trades, positive-result quota or required five-card output. At most five concise expandable cards; routine healthy-balance observations belong in the snapshot brief.
- Scenario first; then calculation-backed action, timing and conditional benefit. Everyday English, mostly white JN branding, subtle horizontal dividers and minimal icons. Alternatives, calculations and scoped review concerns go inside **Explore insight**.
- No authorship/manual-review labels or warning badges on cards. Rate-sheet timestamp and fees stay out of the compact action section but remain in calculation evidence and relevant limitations.

## Non-negotiable safeguards and cost behavior

1. **Do not initiate paid analyst runs or chats without explicit approval.** Implementation/setup approval is not paid-testing approval. Do not click Run analyst, POST `/api/analyse`, or submit chat just to test connectivity.
2. Keep automatic analysis on meaningful workbook/demo-rule changes enabled. Saving meaningful changes while the app runs can therefore incur paid work. For migrations/fixtures, stop the API first and restart after controlled changes.
3. Startup restores saved results and establishes an input baseline **without** paid analysis. Browser refresh, formatting-only saves and prompt-only changes do not trigger paid analysis. Run analyst intentionally requests a fresh run even on unchanged inputs.
4. No new budgets, usage meters, call-count caps, completeness gates, numerical-wording matching, blocking factual reviews or automatic card rewrites. Keep workbook integrity and calculation-input checks.
5. Funding-assessment failures must not suppress other useful suggestions. Invalid tool inputs remain correctable within the existing eight-turn loop.
6. Independent factual review is advisory/non-blocking. Concerns are mapped explicitly to the relevant cards or snapshot overview and shown in detail, never silently guessed from narrative.
7. Provider input compression uses lossless column tables where shorter; immutable local audits keep ordinary JSON, full original evidence and provenance. Do not compress output schemas or model outputs.
8. Never display, commit, upload or include API keys in a transfer ZIP. The original `apikey.txt` and `.env*` are intentionally absent from this bundle. Configure `OPENAI_API_KEY` privately on the new machine.

## Completed implementation

### Core application

- Semantic/stable-save Excel monitoring, Kafka routing, immutable workbook/rule/prompt snapshots, SSE progress, SQLite analyses and suggestion-specific chat.
- Startup restoration, explicit Run analyst button, boot-scoped event processing, deterministic baseline cache and identical-tool-result reuse.
- Per-currency shortfall routing requires a financial calculation; the analyst chooses inputs. The initial valid calculation returns directly without buying an unused assessment memo. The existing tool-enabled writer interprets it, tests worthwhile alternatives and writes cards, followed by independent non-blocking factual review.
- A simple one-shortfall path normally uses three provider requests; a no-shortfall writing/review path normally uses two. Input corrections and optional investigations can add requests; this is not a cap or measured dollar savings.
- Calculation-backed action IDs resolve into displayed figures. Sounds respond to new/changed severity, wording and action terms. Snapshot-pinned follow-ups use the original evidence, not newer workbook values.
- Funding dates are analyst-chosen and proposed, not guaranteed. Python reports baseline/receipt-sensitive first shortfalls and calendar margins; real intraday delivery is unverified.

### Five additional simulated FX scenario types

The user authorized assessing existing data first, synthesizing only missing simulation inputs, then updating the application. This is implemented locally:

1. Reduce an oversized currency position.
2. Avoid buying currency already covered by a scheduled receipt.
3. Compare alternative currencies to sell for funding.
4. Avoid unnecessary opposite/round-trip FX orders.
5. Assess a deliverable forward for a future payment.

Existing balances, holds, reserves, independent Risk_Inputs, capital, aggregate/cash limits, Treasury buy/sell rates, payments/receipts and their statuses were reused unchanged.

`shared/FX_Simulation.xlsx` adds only missing records: foreign-currency exposure limits, fee/interest/settlement assumptions, existing-account permissions, receipt-timing metadata, assumed closures, an unapproved FX planning register, hedge policy and a simulated counterparty. Empty hedge/allocation ledgers explicitly mean none recorded in this simulation. Every new row is labelled synthetic.

Optional calculation tools:
- `assess_fx_trade`: concentration reduction and funding-source comparisons, with fees, account capacity, currency/aggregate exposure and simulated timing.
- `assess_receipt_match`: reduce a linked planned purchase using an existing receipt; no double-counting; checks earmarks, confirmations, timing and selected-receipt delay/absence.
- `assess_round_trip`: compare completing two matching opposite outstanding orders with cancelling both; includes cancellation permission, costs, intermediate liquidity and risk.
- `assess_forward_hedge`: illustrative interest-parity-based forward pricing with spread, coverage/policy/counterparty limits, JMD collateral reservation/release and hypothetical spot sensitivities.

Booked FX/Active hedge cash legs enter the baseline once; exact links to existing payment/receipt legs prevent duplicates. Planned orders do not change current forecasts. Existing hedge collateral must be in JMD holds and its release is counted once. Required funding assessments also enforce the new terms. Immediate legacy conversions cannot supply a recommended action in the extended environment because they omit its dated checks.

These are scenario forecasts and recommendations, **not predictions of future exchange rates**. Synthetic confirmations/calendars/prices do not establish real delivery or executable prices. Carry/opportunity costs, full dated exposure reconciliation and approval readiness remain unverified. Forward tools only support recorded payments/maturities within the current forecast; do not silently extend it.

## Current data, audits and verification

- Last known local verification: **145 backend tests, 19 frontend tests, production build and tracked diff whitespace checks passed**. Tests are offline/mocked, not live-model discovery validation.
- Latest audit inventory at transfer preparation: **62 saved analyses and 12 app chat messages**. The manifest records actual bundle counts. No paid run was used to validate the new scenarios.
- Latest saved analysis: `ba80ea1e1b3a4c32b62fe1bc2cddeffb`, created `2026-10-28T11:22:40.565663`, three cards. It predates the new simulation instructions/tools, so old cards are **not evidence** of their performance.
- Example historical decision: sell GBP for USD 1.05m, arrival November 17, zero-day receipt delay. That was not a newly selected safe date. An offline November 13 fixture was hand-selected, not AI-generated.
- Original workbook hashes were verified unchanged when the extension was added. Backup/manifest: `runtime/experiments/fx-extension-20261028-122458-048505/`. The ZIP also contains current workbooks and historical experiment backups.
- Earlier USD funding fixture changed only `Accounts/CITI-USD-OPS.balance` from 9.4m to 5.4m and `Risk_Inputs/USD.net_fx_exposure` from 9m to 5m, with backups under `runtime/experiments/usd-loan-case-20261027-164057-372263/`. Do not revert those or rebuild other entries.
- Separate LCR/NSFR inputs are synthetic aggregate measures, not available operating balances or proof of regulatory compliance.

## Repository and work status

- Repository: https://github.com/JomoGeorgeSmith/fxtrader.git
- Branch: `orchestrator`; upstream `origin/orchestrator`.
- Existing PR #1: https://github.com/JomoGeorgeSmith/fxtrader/pull/1 — “orchestartion work flow, cost optimized”. It has not been established as merged. Link it to the new session if working on this change.
- Last known pushed commit: `cf8a780`. Main baseline: `930f8fa6e6d8c019b9dd3c07f441f93ef47dd17a`.
- **Substantial later changes are uncommitted/unpushed**, including structured cards, cost/timing improvements, the new simulation support, tests and migration documents. The ZIP preserves both Git history and the actual working tree. Cloning GitHub alone is insufficient.
- Do not reset, clean, discard or overwrite these changes. No new commit, push, merge or paid run was authorized by preparing the transfer.

## Important files

| File | Responsibility |
|---|---|
| `backend/app.py` | API, tool loop, workbook parsing, watcher, snapshots, startup, SSE, pinned chat |
| `backend/analyst.py` | Card schema, evidence catalog, writer, non-blocking review and action resolution |
| `backend/orchestrator.py` | Required per-currency funding interactions and non-blocking failures |
| `backend/calculations.py`, `backend/calculation_cache.py` | Baseline Decimal checks and deterministic caching |
| `backend/funding_plan.py`, `backend/recommended_action.py` | Funding tests, date margins and deterministic card figures |
| `backend/fx_market.py`, `backend/fx_scenarios.py` | Fee/calendar/cash-ledger mechanics and new scenario assessments |
| `backend/simulation_inputs.py`, `backend/synthesize_fx.py` | Optional extension validation and opt-in backed-up missing-tab synthesis |
| `backend/model_context.py` | Compact model views, lossless wire tables and identical-call cache |
| `backend/prompts/fx_analyst.md` | Current v5.5 investigation, financial, timing and friendly-writing instructions |
| `frontend/src/` | Cards, action rows, Explore insight, chat, review notes and sounds |
| `tests/`, `frontend/tests/` | Offline financial/workflow and frontend checks |
| `shared/`, `runtime/` | User-edited Excel and saved analyses/snapshots; ignored by Git but included in transfer |

## Next steps

1. Follow `NEW_MACHINE_SETUP.md`; verify restored hashes, Git working state and saved audit counts.
2. Run offline checks and start locally. Confirm saved results restore without a new run. Keep automatic meaningful-change monitoring enabled.
3. Obtain explicit approval before a paid analyst test. New scenario/date-selection quality and actual post-change dollar savings remain unmeasured.
4. Continue only with the user's next direction. Do not invent new fixtures, force positive results, regenerate workbooks or change the model.
