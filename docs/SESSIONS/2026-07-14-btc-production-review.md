# BTC production review: 14 July 2026

Status: account reconciled flat.

The scheduled executor did not trade because production preflight initially failed on an API IP-whitelist error. After API access was restored, the user manually sold 100 contracts of the 62,800 BTC 0DTE call at 70.0. A put order was placed but received no fill and was cancelled because its executable premium had fallen to approximately USD 1-2.

The call was manually closed reduce-only at 64.1. Gross P&L was USD 0.59, commissions were USD 0.553833, and net P&L was USD 0.036167. The account was then verified to have no BTC position and no active order.

This session is classified as a manual CE-only premium trade, not a completed straddle. It did not follow the frozen paired-entry protocol and was not protected by the automated combined-premium monitor. The exit completed after the intended 17:25 flat time.

The key learning is that declining an uneconomic put leg was reasonable, but the resulting call-only position required a separate defined-risk plan. Going forward, failure of either leg's minimum-credit gate should cancel the whole straddle campaign. A CE-only substitute is permitted only when its own thesis, hard stop, fee-adjusted edge and monitor are defined before entry.
