# Excel-bank FX extension

## Inventory: reuse before synthesis

| Existing source | Reused, not recreated |
|---|---|
| Bank_Balances / Accounts, Reserves | Operating balances, holds, separately protected reserves, account IDs |
| Bank_Balances / Risk_Inputs | Current per-currency FX positions and common capital base |
| Bank_Balances / Liquidity_Inputs | Independent synthetic institution-wide liquidity/funding aggregates |
| Daily_Rate_Sheet / Rates | Bank buying/selling prices, prior reference and timestamps |
| Weekly_Disbursements / Payments, Expected_Receipts | IDs, amounts, dates and original confirmation/commitment statuses |
| policies/demo-rules.json | Aggregate exposure limit, cash cushions, protected reserves, horizon and other demo rules |

The extension never modifies these workbooks or resets edited entries. A setup manifest records their pre/post equality and backup locations. It does not claim the independent risk positions have been reconciled to a real bank's accounting system.

## New workbook: FX_Simulation.xlsx

All added records have `SIM-FX-` IDs, `synthetic=true`, descriptions, and row-level provenance. Read_Me labels all values as assumptions. There is no new operating-balance or receipt-status sheet.

| Missing dataset / tab | Simulation supplied |
|---|---|
| FX_Limits | Absolute native-currency limits translated from equal assumed 8%-of-existing-capital capacity per foreign currency; existing aggregate limit still applies |
| Currency_Terms | Annual decimal interest assumptions (JMD 7.5%, USD 5%, GBP 4.5%, CAD 4%), two business days spot processing, 14:00 cutoff, 5-bp fee plus JMD 1,000 |
| Account_Permissions | Existing operating-account IDs, FX-funding enabled flags, assumed daily FX debit capacities; no reserve-account access |
| Receipt_Timing | Existing receipt IDs, synthetic confirmation references only for existing Confirmed/Committed statuses, delay assumptions and arrival hour; original dates/statuses unchanged |
| Settlement_Closures | Weekend logic plus explicitly assumed December 25 closures for the current/next year; not an official bank calendar |
| FX_Orders | Small unapproved opposite planning orders at existing sheet sides, plus up to two full-payment purchase plans linked to existing obligations/receipts; fees, amendment/cancellation permissions/costs and cash-leg links |
| Hedge_Policy | Deliverable forwards only for foreign currency, assumed 80% coverage cap, 365-day policy tenor and 10-bp pricing spread |
| Counterparties | One simulated approved dealer, existing-capital-based capacity, 5% collateral and JMD 2,500 assumed fee; no actual offer |
| FX_Hedges | Schema-defined empty existing-hedge ledger: no recorded existing hedges in this simulation |
| Receipt_Allocations | Schema-defined empty earmark ledger: no recorded prior allocations in this simulation |

These values create assessment inputs, not a winning recommendation or an approval. Limits, cash checks or costs may reject every trade. The synthesis utility adds **missing tabs only**; it does not overwrite existing tabs or fill them with a fresh portfolio on rerun.

## Mechanics and boundaries

- `assess_fx_trade`: analyst chooses two currencies, destination amount, booking hour, arrival and a nonzero receipt-delay assumption. Python applies sheet sides, cents rounding, fees, operating cash, FX-enabled account capacity, existing same-day FX commitments, currency/aggregate exposure and simulated calendars. Supports both concentration reduction and alternative-source comparisons without ranking them.
- `assess_receipt_match`: requires a real planned order linked to the payment. It checks unallocated receipt/payment capacity and existing committed cover, then compares the original planned purchase with a reduction. The existing receipt is already in both cash forecasts; it is never added again. Partial amendments retain the original fee conservatively. The chosen receipt is also delayed/removed explicitly, including when Confirmed. Expected/unconfirmed receipts and same-day ordering are not promoted into verified readiness.
- `assess_round_trip`: requires two exact opposite outstanding orders with matching intermediate principal and no linked payment needs. It compares completing both with cancelling both, including spread loss, fees, penalties, intermediate liquidity and risk. Unequal/partially offset orders or payment-linked restructuring require further tools and are not silently netted. Cancellation is advice only, including for unapproved planning orders.
- `assess_forward_hedge`: ties a future purchase to one recorded payment and checks existing hedge/committed purchase/receipt allocation cover, policy, counterparty headroom, collateral, cash and timing. A payment/maturity outside the snapshot horizon is rejected even if the policy allows a longer tenor. Maturity must precede the payment date for a passing action; actual intraday delivery is unknown.

Forward illustration, with annual decimal rates and actual calendar days / 365:

`target_per_source = mid_JMD_per_source / mid_JMD_per_target × (1 + target_interest × days/365) / (1 + source_interest × days/365) / (1 + spread_bps/10000)`

The required source principal rounds **up** to cents. The forward fee is paid/reserved at booking; new JMD margin reduces available cash until its release at maturity. Sensitivities at 95%, 100% and 105% of the current indicative spot cross-rate are hypothetical future-spot cases, not forecasts or expected profit. Carry, collateral interest/opportunity costs, real dealer availability and executable terms remain outside this illustration.

Booked orders and Active hedges affect baseline cash. Exact `cash_in_forecast` payment/receipt links prevent double counting and must match currency, amount, date and active status; IDs cannot represent multiple trade legs. Unlinked fees are added once. Active hedge collateral must already be covered by JMD account holds; a separately linked collateral-release receipt suppresses the additional release event. Planned, Settled and Cancelled orders do not add future principal cash legs. Settled trades are assumed reflected in opening positions. Do not add historical trade notionals to current Risk_Inputs again.

Daily-net forecasts are not intraday cash or a complete dated exposure/commitment model. Simulated account capacity conservatively uses current available operating cash and enabled-account limits, net of same-day booked FX commitments; it does not identify a particular account transfer or assume future deposits are accessible at that account. Actual bank terms, confirmations, policies and reconciled positions must replace these assumptions before real use.

## Operation

Stop the API before setup when no paid run is authorized, preview/apply with `python -m backend.synthesize_fx [--apply]`, then restart. Startup restores saved results without analysis; the new workbook is watched and snapshotted on later meaningful changes. Saved results/chat remain tied to their old snapshot, not silently upgraded. A fresh paid run must be explicitly requested/approved when testing the analyst's new judgment.

Offline tests cover workbook preservation/idempotency, relationships, ledger linking, fees, permissions, timing, receipt stress, cancellations, collateral, policy limits, scenario-specific cards and the existing writer/reviewer path. They establish calculation behavior, **not** a live model's ability to discover or choose the best action.
