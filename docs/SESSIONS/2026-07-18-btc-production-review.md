# BTC production review: 18 July 2026

Status: profitable campaign after fees; account reconciled flat.

The 16:55 scheduler and production preflight ran successfully. At 17:00, the executor selected the 64,000 same-day BTC call and put and entered 125 contracts per leg. The call sold at 26 and the put at 5, producing a combined credit of 31. The combined executable-premium stop was armed at 46.5 and never triggered.

The put was manually closed at 0.8 around 17:08:42 because little residual premium remained. The remaining call was held until the planned time exit and was automatically bought back at 27 at 17:24:31. Call gross P&L was small and put gross P&L was small Campaign gross P&L was small; Delta-reported commissions were as charged; net P&L was **small and positive**.

The low-premium put exit locked most of the leg's available decay: only a little additional gross profit remained if it expired at zero. However, this manual intervention changed the structure from a straddle to a naked short call. The running executor did not reconcile the position change and attempted to close the already-flat put after closing the call. Delta rejected that redundant reduce-only order, after which emergency reconciliation correctly confirmed zero BTC positions and zero active orders.

The key learning is that manual leg management and automated monitoring must not diverge. A future position-reconciliation loop should detect a manually closed leg, switch to the remaining-leg manager without resetting the original campaign risk, and generate time-exit orders only from current broker positions.
