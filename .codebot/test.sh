#!/usr/bin/env bash
#
# CodeBot test helper for openstates-core.
#
# Runs the real pytest suite against THIS checkout (the clone CodeBot is
# editing) in a fully isolated Docker stack, mirroring this repo's own CI
# (.github/workflows/test.yml): a throwaway Postgres plus a `core` service
# built from this repo's own Dockerfile (entrypoint `poetry run`).
#
# The devos `test-ticket` skill invokes this via the required `.codebot/test.sh`
# entrypoint (Step 0 of that skill looks for that exact path at the repo root).
#
# Usage:
#   .codebot/test.sh [TICKET_KEY] [extra pytest args...]
#
#   TICKET_KEY is optional and used only for labelling the run and scoping the
#   compose project/image tag. Any args after it are passed through to pytest
#   (e.g. `openstates/tests/test_something.py`). With no extra args, the full
#   `openstates` suite runs, matching CI's own invocation.
#
# Exit code is pytest's exit code (0 = all tests passed).

set -euo pipefail

# --- AGENTS-250: the Mac's test Docker VM, never production's ------------------
# On the Mac, Docker is two VMs: production's (CAMS, api-v3, the dev broker and its copy of production) and a test
# VM on /Volumes/DDP-HOT for every throwaway test stack and image build. ddp-agents'
# deployment/scripts/colima-test.sh is the one place that knows where the test VM is; when it exists, this script
# takes DOCKER_HOST from it and stops (exit 2) if the test VM is not available, instead of building on production.
# Elsewhere (no such file) DOCKER_HOST is left as it is. DDP_COLIMA_TEST_SH overrides the path; empty turns it off.
COLIMA_TEST_SH="${DDP_COLIMA_TEST_SH-/Users/agentsmith/Developer/repos/ddp-agents/deployment/scripts/colima-test.sh}"
if [[ -n "${COLIMA_TEST_SH}" && -r "${COLIMA_TEST_SH}" ]]; then
  if ! WHY="$(bash "${COLIMA_TEST_SH}" check 2>&1)"; then
    echo "ERROR: the test Docker VM is not available, so nothing was built: ${WHY}" >&2
    exit 2
  fi
  DOCKER_HOST="$(bash "${COLIMA_TEST_SH}" docker-host)"
  export DOCKER_HOST
  if ! docker info >/dev/null 2>&1; then
    echo "ERROR: the test Docker VM is set up but not running (${DOCKER_HOST}), so nothing was built." >&2
    echo "       Start it with: ${COLIMA_TEST_SH} colima start" >&2
    exit 2
  fi
  echo ">>> Docker: the test VM (${DOCKER_HOST})"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.test.yml"

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "ERROR: ${COMPOSE_FILE} not found" >&2
  exit 2
fi

TICKET_KEY="${1:-run}"
shift || true
TEST_ARGS=("$@")

SAFE_KEY="$(echo "${TICKET_KEY}" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-' | sed 's/-\{2,\}/-/g; s/^-//; s/-$//')"
PROJECT="codebot-test-${SAFE_KEY:-run}-$$"

# Image tag scoped by ticket key, not a fixed shared name — see api-v3/
# ddp-broker-py's identical comment: CAMS's worker pool runs tasks
# concurrently, and a shared tag would let one ticket's build retag the
# image a different ticket's `compose run` is about to use.
export CODEBOT_TEST_IMAGE="${CODEBOT_TEST_IMAGE:-codebot-openstates-core:${SAFE_KEY:-run}}"

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "ERROR: neither 'docker compose' nor 'docker-compose' is available" >&2
  exit 2
fi

compose() { "${DC[@]}" -p "${PROJECT}" -f "${COMPOSE_FILE}" "$@"; }

cleanup() {
  compose down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo ">>> CodeBot isolated test run for ${TICKET_KEY} (openstates-core)"
echo ">>> project=${PROJECT}  image=${CODEBOT_TEST_IMAGE}"

echo ">>> building core image from this checkout..."
compose build core

if [[ ${#TEST_ARGS[@]} -eq 0 ]]; then
  TEST_ARGS=(--cov openstates --ds=openstates.test_settings -v openstates)
fi

echo ">>> running: pytest ${TEST_ARGS[*]}"
compose run --rm core pytest "${TEST_ARGS[@]}"
