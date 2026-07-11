# Security

## Secrets

Never commit `.env`, API keys, API secrets, HMAC signatures, wallet balances,
authenticated fills, or order history. Use `.env.example` only as a template.

Delta API keys must use IP whitelisting. Prefer separate read-only and trading
keys and grant the minimum permissions required.

## Reporting a vulnerability

Please open a GitHub security advisory rather than a public issue if you find a
credential leak, signing flaw, duplicate-order risk, or other security problem.

## Execution boundary

This repository contains research and assisted-execution documentation. It does
not contain an enabled unattended live-order engine. Do not add live execution
without dry-run mode, idempotent client order IDs, account loss caps, margin
guards, heartbeat monitoring, duplicate prevention, and a tested kill switch.
