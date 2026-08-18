#!/usr/bin/env bash
# Drive Creo on a remote Windows box from macOS/Linux over SSH.
#
#   creo-remote.sh <ClassName> [--timeout SEC] [--done REGEX] [--no-kill]
#   creo-remote.sh --check     verify the remote Creo/JDK environment
#   creo-remote.sh --status    is Creo running, is an interactive session alive
#   creo-remote.sh --kill      kill leftover Creo processes, free the license seat
#   creo-remote.sh --push      only sync creo-run.bat (+ <Class>.java) to the box
#
# Needs on the Windows box: WSL reachable over ssh, Creo, a JDK, and a logged-in
# interactive session (RDP counts). See SKILL.md.
set -euo pipefail

# ============================ EDIT THIS BLOCK ==============================
HOST="${CREO_HOST:-winbox}"                                    # ssh alias of the WSL on the Windows machine
WIN_USER="${CREO_WIN_USER:-dev}"                               # Windows account that is logged in interactively
WORK_WIN="${CREO_WORK:-C:\\creo-auto}"                         # working directory, Windows path
CREO_ROOT="${CREO_ROOT:-C:\\Program Files\\PTC\\Creo 13.4.0.0}"
JDK_WIN="${CREO_JDK:-C:\\Program Files\\Eclipse Adoptium\\jdk-25.0.3+9}"
CODEPAGE="${CREO_CODEPAGE:-GBK}"                               # Windows console codepage: GBK (zh), CP1252 (en)
TASK="${CREO_TASK:-CreoRun}"                                   # scheduled task name
# ===========================================================================

SYS32=/mnt/c/Windows/System32
NOISE='post-quantum|store now|openssh.com/pq|vulnerable'
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# C:\creo-auto -> /mnt/c/creo-auto
_drive=$(printf '%s' "${WORK_WIN%%:*}" | tr 'A-Z' 'a-z')
_rest=$(printf '%s' "${WORK_WIN#*:}" | tr '\\' '/')
WORK_WSL="/mnt/${_drive}${_rest}"

# Run a bash snippet on the Windows box's WSL.
# Every .exe below needs </dev/null, or it swallows the rest of this snippet as
# its own stdin and the remaining commands silently never run.
rsh() { ssh -o BatchMode=yes "$HOST" "bash -s" 2>&1 | grep -Ev -i "$NOISE" || true; }
dec() { iconv -f "$CODEPAGE" -t UTF-8//TRANSLIT 2>/dev/null || cat; }
put() { sed 's/$/\r/' | ssh -o BatchMode=yes "$HOST" "cat > $1"; }   # text -> Windows file, CRLF

kill_creo() {
  # nmsd is Creo's message daemon. It lingers ~300s and keeps a handle on the
  # working directory, which blocks deleting or moving it.
  echo "for p in xtop.exe parametric.exe nmsd.exe; do $SYS32/taskkill.exe /IM \$p /F </dev/null >/dev/null 2>&1; done; echo creo-stopped" | rsh
}

status() {
  echo "$SYS32/tasklist.exe /FI 'IMAGENAME eq xtop.exe' </dev/null 2>&1; $SYS32/query.exe user </dev/null 2>&1" | rsh | dec
}

# cmd.exe warns when its CWD is a UNC path, so always enter /mnt/c first.
remote_check() {
  echo "cd /mnt/c && $SYS32/cmd.exe /c \"${WORK_WIN}\\creo-run.bat --check\" </dev/null 2>&1" | rsh | dec
}

# Copy creo-run.bat from the sibling creo-jlink skill, with this file's
# CREO_ROOT / JDK baked in, plus <Class>.java from the current directory.
# Missing sibling is fine: whatever is already on the box gets used.
push() {
  local bat="$HERE/../creo-jlink/creo-run.bat" cls="${1:-}"
  echo "mkdir -p $WORK_WSL" | rsh >/dev/null
  if [ -f "$bat" ]; then
    # sed eats backslashes and expands & in the replacement - neutralise both
    local cr=${CREO_ROOT//\\/\\\\}; cr=${cr//&/\\&}
    local jd=${JDK_WIN//\\/\\\\};   jd=${jd//&/\\&}
    sed -e "s|^set \"CREO_ROOT=.*|set \"CREO_ROOT=$cr\"|" \
        -e "s|^set \"JDK=.*|set \"JDK=$jd\"|" "$bat" | put "$WORK_WSL/creo-run.bat"
    echo "==> pushed creo-run.bat (CREO_ROOT + JDK applied)"
  else
    echo "==> no local creo-run.bat, using the copy already on the box"
  fi
  if [ -n "$cls" ] && [ -f "$cls.java" ]; then
    put "$WORK_WSL/$cls.java" < "$cls.java"
    echo "==> pushed $cls.java"
  fi
}

case "${1:-}" in
  --kill)   kill_creo;      exit 0;;
  --status) status;         exit 0;;
  --push)   push "${2:-}";  exit 0;;
  --check)  push; remote_check; exit 0;;
esac

CLS=${1:?usage: creo-remote.sh <ClassName> [--timeout SEC] [--done REGEX] [--no-kill] | --check | --status | --kill | --push}
shift
TIMEOUT=1800; DONE='_DONE'; KILL=1
while [ $# -gt 0 ]; do
  case $1 in
    --timeout) TIMEOUT=$2; shift 2;;
    --done)    DONE=$2;    shift 2;;
    --no-kill) KILL=0;     shift;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done

push "$CLS"

echo "==> checking the remote environment"
CHK=$(remote_check)
echo "$CHK"
case "$CHK" in *'[FAIL]'*) echo "aborting" >&2; exit 1;; esac

# Baseline timestamp: only logs written after this point belong to this run.
START=$(echo 'date +%s' | rsh | tr -d '[:space:]')

echo "==> launching Creo in the interactive session (schtasks /it)"
kill_creo >/dev/null
{ echo '@echo off'; echo "call \"${WORK_WIN}\\creo-run.bat\" $CLS"; } | put "$WORK_WSL/_run_task.bat"
cat <<EOF | rsh | dec | tail -2
$SYS32/schtasks.exe /delete /tn $TASK /f </dev/null >/dev/null 2>&1
$SYS32/schtasks.exe /create /tn $TASK /tr "${WORK_WIN}\\_run_task.bat" /sc ONCE /st 00:00 /ru $WIN_USER /it /f </dev/null 2>&1
$SYS32/schtasks.exe /run /tn $TASK </dev/null 2>&1
EOF

echo "==> polling $WORK_WIN for /$DONE/ in *.log (timeout ${TIMEOUT}s, every 20s)"
DEADLINE=$((SECONDS + TIMEOUT))
while [ $SECONDS -lt $DEADLINE ]; do
  HIT=$(echo "find $WORK_WSL -name '*.log' -newermt @$START -exec grep -l -E '$DONE' {} + 2>/dev/null" | rsh)
  if [ -n "$HIT" ]; then
    echo "==> done: $HIT"
    echo "tail -n 20 $HIT" | rsh
    if [ $KILL -eq 1 ]; then kill_creo >/dev/null; echo "==> Creo stopped, license seat freed"; fi
    exit 0
  fi
  sleep 20
done

echo "==> timed out after ${TIMEOUT}s without /$DONE/. Logs written by this run:" >&2
echo "find $WORK_WSL -name '*.log' -newermt @$START -exec tail -n 15 {} + 2>/dev/null" | rsh >&2
echo "Creo is still running. Poll again, or give up with: creo-remote.sh --kill" >&2
exit 1
