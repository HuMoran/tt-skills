---
name: creo-remote
description: Drive Creo on a remote Windows box from macOS/Linux over SSH — push the app, compile, launch into the interactive session, poll for completion, free the license seat. Use for 远程跑 Creo / 远程导出, SSH 到 Windows 跑 CAD, schtasks /it, WSL interop, or when starting Creo over SSH fails with no GUI context.
---

# Creo Remote — Operation Skill

Run a Creo J-Link batch on a Windows machine you are not sitting at. One command from the Mac: push the app, compile, launch, wait, clean up.

Companion to **`creo-jlink`**, which owns the recipe (JVM version, `protk.dat`, writing the Java app) and ships `creo-run.bat`. This skill only adds the remote plumbing, and reuses that `creo-run.bat` on the far end rather than reimplementing it.

## Prerequisites on the Windows box

| | Why |
|---|---|
| WSL, reachable by `ssh <alias>` | the only sane way in — see "Why WSL" below |
| Creo + a JDK new enough for `otk.jar` | `creo-remote.sh --check` verifies this |
| **An interactive session that is logged on** | Creo needs a GUI rendering context. An RDP session counts, and it may be *disconnected* — it just has to exist |

That last one is not optional and is the most common reason a run does nothing. `--status` shows it.

## Setup

Edit the block at the top of `creo-remote.sh` (or set the matching `CREO_*` environment variables):

```bash
HOST=winbox                                  # ssh alias of the WSL on the Windows machine
WIN_USER=dev                                 # Windows account logged in interactively
WORK_WIN='C:\creo-auto'                      # working directory, Windows path
CREO_ROOT='C:\Program Files\PTC\Creo 13.4.0.0'
JDK_WIN='C:\Program Files\Eclipse Adoptium\jdk-25.0.3+9'
CODEPAGE=GBK                                 # Windows console codepage: GBK (zh), CP1252 (en)
```

`~/.ssh/config` needs a matching `Host winbox` entry pointing at the WSL sshd with key auth.

## Use

```bash
./creo-remote.sh --status              # is Creo running, is a session logged in
./creo-remote.sh --check               # Creo found, JDK new enough for otk.jar
./creo-remote.sh MyBatch               # push, compile, launch, poll, clean up
./creo-remote.sh MyBatch --timeout 3600 --done 'BATCH_DONE|FATAL'
./creo-remote.sh --kill                # kill leftovers, free the license seat
```

`MyBatch.java` in the current directory is pushed automatically; otherwise the copy already on the box is used. `creo-run.bat` is taken from the sibling `creo-jlink` skill with your `CREO_ROOT` / `JDK_WIN` patched in, so the far end never drifts from your config.

Polling looks for `--done` (default `_DONE`) in any `*.log` under the working directory **written after launch**. Your app must print such a marker on its last line — see `creo-jlink`.

## Why `schtasks /it`

An SSH session is non-interactive and has no GUI rendering context. Launch `parametric.bat` directly over SSH and Creo fails. So the run is delivered into a session that does have one:

```
schtasks /create /tn CreoRun /tr <launcher>.bat /sc ONCE /st 00:00 /ru <user> /it /f
schtasks /run   /tn CreoRun
```

`/it` = run in that user's interactive session, and it requires `/ru`. The session may be disconnected; it only has to exist. `schtasks /run` returns immediately while Creo runs asynchronously — hence polling a log rather than waiting on the command.

## Why WSL, not PowerShell over SSH

Windows' own sshd drops you into PowerShell, where a non-English console codepage mangles inline quotes and redirections, and the usual fix is base64 `-EncodedCommand` for every call. Going in through WSL and invoking Windows programs by **interop** avoids all of it — but three rules are non-negotiable:

1. **Full paths.** WSL's `PATH` has no `System32`. `schtasks.exe` alone is `command not found`; use `/mnt/c/Windows/System32/schtasks.exe`.
2. **`</dev/null` on every `.exe`.** Otherwise it consumes the rest of your `bash -s` script as its own stdin, and every later command silently never runs. This one is genuinely hard to debug — the script just stops, with no error.
3. **Decode the output.** Windows console output is in the OEM codepage (GBK on a Chinese install), not UTF-8. Pipe it through `iconv -f "$CODEPAGE" -t UTF-8//TRANSLIT`.

Path mapping is `C:\X` ↔ `/mnt/c/X`: Windows paths as arguments to `.exe`, `/mnt/c` paths for reads and writes on the WSL side.

## Cleanup

A finished run kills `xtop.exe`, `parametric.exe` and `nmsd.exe`. The last one is Creo's message daemon: it outlives Creo by ~300s and keeps a handle on the working directory, so until it exits that directory cannot be deleted or moved.

Most sites have a single license seat. `--no-kill` keeps Creo up for a follow-up run; anything else should end with the seat released.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `schtasks /run` succeeds, nothing happens | no interactive session — check `--status`, reconnect RDP |
| `[FAIL] Creo not found` with a mangled path | `CREO_ROOT` lost its backslashes — quote it in single quotes |
| Permission denied on ssh | key auth not set up for the WSL sshd |
| Remote script stops halfway, no error | an `.exe` without `</dev/null` |
| Output is mojibake | wrong `CODEPAGE` for that Windows install |
| Poll times out but the app finished | your app's marker does not match `--done` |
| Working directory cannot be deleted | `nmsd.exe` still holding it — `--kill`, or wait ~300s |
