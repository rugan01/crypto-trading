# BTC production review: 13 July 2026

Status: account reconciled flat.

## Broker-reconciled result

The scheduled executor passed preflight but failed to establish the paired straddle. The put filled while the call IOC did not, and the old implementation immediately flattened the put instead of using the documented missing-leg recovery window. That attempt produced gross P&L of $0.40, commissions of $0.34692, and net P&L of $0.05308. It is classified as an execution failure.

The user then entered the 63,000 BTC short straddle manually, 100 contracts per leg, for a combined credit of 95. The new manager adopted the position, armed a combined executable-premium stop at 142.5, and completed the forced exit just after 17:25. The stop did not trigger. This campaign produced gross P&L of $4.78, commissions of $0.587286, and net P&L of $4.192714.

Combined day: gross $5.18, commissions $0.934206, net $4.245794. Positive P&L does not make this a protocol-compliant observation.

## Three-day failure pattern

- 11 July: multi-minute leg gap, incorrect stop-trigger assumptions, and settlement instead of the planned 17:25 exit.
- 12 July: scheduler miss followed by a production-confirmation propagation failure; later manual execution again had a multi-minute leg gap.
- 13 July: scheduler worked, but the executor gave the missing leg effectively no recovery time and generated an avoidable commission-bearing round trip.

## Changes required for the next production session

- concurrent 25-contract matched slices;
- 20-second total entry window and up to 10 seconds of missing-leg repricing;
- combined-credit floor on every retry;
- absolute 17:24:30 exit start rather than 25 minutes after a late fill;
- 16:55 read-only production preflight with Telegram result;
- emergency flat reconciliation when the main session exits abnormally;
- startup, stop-arm, halt, and closure Telegram evidence.

The testnet execution rehearsal was blocked by a testnet API-key IP whitelist mismatch. Production authenticated reads remained valid. Unit coverage for the missing-leg retry path passed.
