#!/bin/bash
# Run pytest for the dianping-5 (unify) workdir with the shared venv site-packages.
# Usage: ./run_tests.sh [pytest args...]
# TMPDIR is workdir-local so parallel blocks don't share /tmp test DBs.
cd "$(dirname "$0")/apps/api" || exit 1
export PYTHONPATH="$HOME/workspace/coupon-go-fullstack/.venv/lib/python3.12/site-packages"
export TMPDIR="$HOME/workspace/coupon-go-dianping-5/.pytest-tmp"
export COUPON_GO_ENV=dev
export SEED_ON_STARTUP=0
mkdir -p "$TMPDIR"
exec /usr/bin/python3.12 -m pytest "$@"
