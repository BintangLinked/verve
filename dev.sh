#!/usr/bin/env bash
# Runs the API, worker and frontend from a single terminal.
#
# Usage (from the project root folder):
#   ./dev.sh start     start all three in the background
#   ./dev.sh stop      stop all three
#   ./dev.sh restart   stop, then start (required after changing backend/.env)
#   ./dev.sh status    show which are running
#   ./dev.sh logs      follow the logs of all three (Ctrl+C to exit; the processes keep running)
#
# Purpose: replaces three manual terminals. PIDs and logs are stored in .run/.
# Relations: runs backend/ (uvicorn and app.worker) and frontend/ (npm run dev).
# The script only stops processes that it started itself (based on .run/*.pid);
# other processes on the same port are not touched; the script only reports.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$ROOT/.run"
API_PORT=8000
WEB_PORT=3000
mkdir -p "$RUN"

# Command for each service: name|working folder|command
SERVICES=(
  "api|backend|.venv/bin/uvicorn app.main:app --port $API_PORT"
  "worker|backend|.venv/bin/python -m app.worker"
  "web|frontend|npm run dev -- --port $WEB_PORT"
)

port_busy() { ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN; }

# A service is considered running if the PID in the file is still alive.
is_running() {
  local pidfile="$RUN/$1.pid"
  [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

start_one() {
  local name="$1" dir="$2" cmd="$3"
  if is_running "$name"; then echo "$name: already running (PID $(cat "$RUN/$name.pid"))"; return 0; fi
  # Port already used by another process (not from this script): do not override it, only report.
  if [[ "$name" == "api" ]] && port_busy "$API_PORT"; then
    echo "api: FAILED, port $API_PORT is used by another process. Stop it first (check: ss -ltnp | grep :$API_PORT)."; return 1
  fi
  # The worker has no port: prevent two workers at once (from a manual terminal) so that events are not processed twice.
  if [[ "$name" == "worker" ]] && pgrep -f "python[0-9.]* -m app\.worker" >/dev/null; then
    echo "worker: FAILED, another app.worker process already exists (check: pgrep -af app.worker). Stop it first."; return 1
  fi
  if [[ "$name" == "web" ]] && port_busy "$WEB_PORT"; then
    echo "web: FAILED, port $WEB_PORT is used by another process. Stop it first (check: ss -ltnp | grep :$WEB_PORT)."; return 1
  fi
  # setsid: each service becomes its own process group, so that stop can terminate all its child processes
  # (npm run dev spawns a next-server process).
  # The PID is recorded from inside the process itself ($$ before exec): setsid may fork, so $! from outside is not accurate.
  (cd "$ROOT/$dir" && setsid bash -c "echo \$\$ >'$RUN/$name.pid'; exec $cmd" >"$RUN/$name.log" 2>&1 < /dev/null &)
  sleep 0.5
  echo "$name: started (PID $(cat "$RUN/$name.pid")), log: .run/$name.log"
}

stop_one() {
  local name="$1" pidfile="$RUN/$1.pid"
  if ! is_running "$name"; then echo "$name: not running"; rm -f "$pidfile"; return 0; fi
  local pid; pid="$(cat "$pidfile")"
  kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null   # TERM to the whole group
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
  kill -0 "$pid" 2>/dev/null && kill -9 -- "-$pid" 2>/dev/null
  rm -f "$pidfile"
  echo "$name: stopped"
}

wait_api() {
  for _ in $(seq 1 30); do
    curl -fs -m 2 "http://localhost:$API_PORT/health" >/dev/null 2>&1 && { echo "api: healthy (/health OK)"; return 0; }
    sleep 1
  done
  echo "api: no response from /health after 30 seconds, see .run/api.log"; return 1
}

cmd_start() {
  local rc=0
  for s in "${SERVICES[@]}"; do IFS='|' read -r n d c <<<"$s"; start_one "$n" "$d" "$c" || rc=1; done
  wait_api || rc=1
  echo "Dashboard: http://localhost:$WEB_PORT (the frontend needs a few seconds to be ready)"
  return $rc
}

cmd_stop() { for s in "${SERVICES[@]}"; do IFS='|' read -r n _ _ <<<"$s"; stop_one "$n"; done; }

cmd_status() {
  for s in "${SERVICES[@]}"; do
    IFS='|' read -r n _ _ <<<"$s"
    if is_running "$n"; then echo "$n: running (PID $(cat "$RUN/$n.pid"))"; else echo "$n: stopped"; fi
  done
  port_busy "$API_PORT" && echo "port $API_PORT: in use" || echo "port $API_PORT: free"
  port_busy "$WEB_PORT" && echo "port $WEB_PORT: in use" || echo "port $WEB_PORT: free"
}

case "${1:-}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  logs)    tail -n 20 -F "$RUN/api.log" "$RUN/worker.log" "$RUN/web.log" ;;
  *)       echo "Usage: ./dev.sh {start|stop|restart|status|logs}"; exit 2 ;;
esac
