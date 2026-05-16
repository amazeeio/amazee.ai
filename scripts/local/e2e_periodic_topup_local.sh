#!/usr/bin/env bash
set -euo pipefail

echo "Running baseline section of consolidated E2E script..."
RUN_BASELINE=1 RUN_WEBHOOK=0 "$(dirname "$0")/e2e_periodic_stripe_plus_topup_local.sh"
