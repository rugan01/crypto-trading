#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x /opt/homebrew/bin/python3 ]]; then
  python=/opt/homebrew/bin/python3
else
  python=$(command -v python3)
fi

"$python" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'import ssl, sys; print(sys.executable); print(ssl.OPENSSL_VERSION)'
