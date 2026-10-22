#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
python -m unittest discover -s tests -v
for script in scripts/*.sh; do bash -n "$script"; done
mkdir -p build
if [[ -n "${BICEP_BIN:-}" ]]; then
  "$BICEP_BIN" build infra/main.bicep --outfile build/foundation.json
  "$BICEP_BIN" build infra/worker.bicep --outfile build/worker.json
else
  az bicep build --file infra/main.bicep --outfile build/foundation.json
  az bicep build --file infra/worker.bicep --outfile build/worker.json
fi
python tests/check_templates.py build/foundation.json build/worker.json
