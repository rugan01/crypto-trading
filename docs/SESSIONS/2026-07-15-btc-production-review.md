# BTC production review: 15 July 2026

Status: fully automated campaign closed on the planned combined-premium stop; account reconciled flat.

The recurring 16:55 scheduler started correctly, both Telegram readiness messages were delivered, and production preflight passed. At 17:00 the executor selected the 64,600 BTC same-day ATM call and put. It entered 100 contracts per leg as four concurrent matched 25-contract slices. The last put slice needed four retries but completed inside the bounded recovery window, so no unmatched exposure remained.

Average credits were 57.25 for the call and 18.00 for the put, giving 75.25 combined. The executable combined stop was armed at 112.875. At 17:06:59, buyback reached 114.9 for the second consecutive observation and triggered the stop. The call closed at 100 and the put at 2.2. Gross P&L was -$2.6950, Delta-reported commissions were $0.7328685, and net P&L was -$3.4278685.

This is a strategy loss but an important automation success: scheduler, paired entry, missing-leg recovery, stop detection, two-leg exit and final reconciliation all completed without manual intervention.

One defect remains. Exit submission began 5.28 seconds after stop confirmation because the program waited for the WebSocket thread to shut down. The fill happened to improve during the delay, but that outcome must not validate the sequencing. Exit orders are now submitted before waiting for WebSocket termination.

From 16 July, requested size is 200 contracts per leg in four matched 50-contract slices. All existing leverage, liquidity, spread, credit, projected-free-margin and planned-loss gates remain unchanged. If 200 contracts cannot pass those gates, the result is no trade—not a reduced safeguard.
