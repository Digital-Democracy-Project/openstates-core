#!/usr/bin/env bash
# Checks that .codebot/test.sh runs every docker command on the Mac's TEST Docker VM (AGENTS-250), with a fake
# `docker` and a stub of ddp-agents' colima-test.sh: no real Docker is used. Run: bash .codebot/test-docker-host.sh
set -uo pipefail
HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/test.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/codebot-docker-host.XXXXXX")" && [ -n "$T" ] || { echo "cannot make a temp folder" >&2; exit 1; }
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"
# Fake docker: logs "DOCKER_HOST args" and succeeds, except `info` when FAKE_DOCKER_DOWN=1.
cat > "$T/bin/docker" <<'D'
#!/usr/bin/env bash
echo "${DOCKER_HOST:-<unset>} $*" >> "$FAKE_DOCKER_LOG"
[ "$1" = "info" ] && [ "${FAKE_DOCKER_DOWN:-0}" = 1 ] && exit 1
exit 0
D
# Stub colima-test.sh: `check` fails when STUB_CHECK_FAILS=1; `docker-host` prints the test socket.
cat > "$T/colima-test.sh" <<'C'
case "$1" in
  check) [ "${STUB_CHECK_FAILS:-0}" = 1 ] && { echo "DDP-HOT is not mounted"; exit 3; }; exit 0 ;;
  docker-host) echo "unix:///fake/test/docker.sock" ;;
esac
C
chmod +x "$T/bin/docker"
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "ok   - $1"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL - $1"; }
CALLER_HOST="unix:///fake/production.sock"   # the caller's own DOCKER_HOST; empty = the caller has none
run() {  # run the helper with a fresh log; extra env as arguments
  : > "$T/log"
  [ -n "$CALLER_HOST" ] && set -- DOCKER_HOST="$CALLER_HOST" "$@"
  env -u DOCKER_HOST PATH="$T/bin:$PATH" FAKE_DOCKER_LOG="$T/log" "$@" \
    bash "$HELPER" AGENTS-250 >"$T/out" 2>&1
}

run DDP_COLIMA_TEST_SH="$T/colima-test.sh" STUB_CHECK_FAILS=1; rc=$?
[ "$rc" = 2 ] && ok "test VM unavailable: exit 2" || bad "test VM unavailable: exit $rc, want 2"
[ ! -s "$T/log" ] && ok "test VM unavailable: no docker call" || bad "test VM unavailable: docker was called: $(cat "$T/log")"

run DDP_COLIMA_TEST_SH="$T/colima-test.sh" FAKE_DOCKER_DOWN=1; rc=$?
[ "$rc" = 2 ] && ok "test VM down: exit 2" || bad "test VM down: exit $rc, want 2"
grep -q -v '^unix:///fake/test/docker.sock info' "$T/log" && bad "test VM down: called more than docker info: $(cat "$T/log")" || ok "test VM down: only docker info was called"

run DDP_COLIMA_TEST_SH="$T/colima-test.sh"; rc=$?
[ "$rc" = 0 ] && ok "test VM up: exit 0" || bad "test VM up: exit $rc: $(tail -3 "$T/out")"
grep -q ' build' "$T/log" && ok "test VM up: an image was built" || bad "test VM up: no build call: $(cat "$T/log")"
grep -q -v '^unix:///fake/test/docker.sock ' "$T/log" && bad "test VM up: a docker call not on the test VM: $(grep -v '^unix:///fake/test/docker.sock ' "$T/log" | head -3)" || ok "test VM up: every docker call on the test VM"

CALLER_HOST=""; run DDP_COLIMA_TEST_SH="$T/colima-test.sh"; rc=$?; CALLER_HOST="unix:///fake/production.sock"
[ "$rc" = 0 ] && grep -q ' build' "$T/log" && ! grep -q -v '^unix:///fake/test/docker.sock ' "$T/log" \
  && ok "caller without DOCKER_HOST: every docker call on the test VM" || bad "caller without DOCKER_HOST: rc=$rc log=$(head -3 "$T/log")"

run DDP_COLIMA_TEST_SH=""; rc=$?
[ "$rc" = 0 ] && grep -q '^unix:///fake/production.sock .*build' "$T/log" && ok "check off: the caller's DOCKER_HOST is kept" || bad "check off: rc=$rc log=$(head -3 "$T/log")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
