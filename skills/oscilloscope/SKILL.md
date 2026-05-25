---
name: oscilloscope
description: Drive a benchtop oscilloscope via VISA / USBTMC for hardware bring-up and waveform verification. Supports two scopes auto-detected at connect time — Keysight / Agilent InfiniiVision DSO5000-series (tested DSO5012A) and RIGOL DS1000Z-series (tested DS1102Z-E). Use this skill ANY time the user mentions the scope, oscilloscope, 示波器, DSO5012A, DS1102Z-E, DS1000Z, capturing waveforms, taking screenshots from the scope, CSV waveform data, SCPI commands, trigger setup, or pre/post-bridge measurements. Also use when the user reports problems like "trace is missing", "trigger isn't firing", "波形抓不到", or wants to verify firmware output on hardware. Self-contained — bundles the helper script and a venv bootstrap so it works without `~/Work/scope/` being set up. Knows the project-specific quirks (×100 probe lock on Keysight, BMP-not-PNG on Keysight, channel-display footgun, AUTO-SCALE override, RIGOL USBTMC pipe-stall recovery, RIGOL binary-block settle delay).
---

# Oscilloscope Operation Skill (Keysight + RIGOL)

Drives a benchtop scope via a bundled `scope.py`. Vendor auto-detected at connect time via `*IDN?`. Built from multiple emst-core hardware bring-up sessions.

## Supported hardware

| Vendor / Model | Detection | Probe handling | Screenshot |
|---|---|---|---|
| Keysight / Agilent InfiniiVision DSO5012A (fw 06.10) | `*IDN?` contains "KEYSIGHT" or "AGILENT" | AutoProbe locks ×100 — read-only | BMP only (`:DISP:DATA? BMP, SCReen, COLor`) → convert with `sips` |
| RIGOL DS1000Z (tested DS1102Z-E fw 00.06.02) | `*IDN?` contains "RIGOL" | `:CHAN:PROBe` is settable | PNG native (`:DISP:DATA? ON,OFF,PNG`) — viewer-ready |

The two scopes do not need to be plugged in at the same time. The script auto-discovers whichever USB::INSTR resource is present (`scope.py` with no `--addr`).

## Hardware-specific notes

### Keysight DSO5012A
- Default VISA addr: `USB0::2391::5987::MY50340343::0::INSTR`
- ×100 passive probes AutoProbe-locked; `:CHAN1:PROB 1` returns SCPI `-222 "Data out of range"`. The display already shows post-attenuation voltage.
- Old firmware: `:DISP:DATA?` only outputs **BMP**. Save as `.bmp`, convert with `sips -s format png foo.bmp --out foo.png`.

### RIGOL DS1102Z-E
- Default VISA addr: `USB0::6833::1303::DS1ZE224209419::0::INSTR`
- USB endpoints differ from Keysight: bulk OUT = `0x03`, bulk IN = `0x82` (the recover routine auto-discovers).
- DS1102Z-E is **2-channel** (CH1/CH2). The `Vendor` adapter restricts channels to (1,2) when the IDN contains `DS1102Z-E`; DS1054Z/DS1074Z/DS1104Z would expose 4.
- 数据采集深度 `:ACQuire:MDEPth` defaults to `AUTO` (= 1200 points = screen depth). For real deep-memory capture, set it first: `scope.py raw ':ACQuire:MDEPth 1200000'` (12k/120k/1.2M/12M/24M are the discrete options).
- The PNG screenshot is ~30–60 KB.

## Bootstrap (first run on a new machine)

The skill bundles `scripts/scope.py` plus `requirements.txt` and a `setup.sh`. To create the venv (idempotent):

```bash
bash "$HOME/.claude/skills/oscilloscope/scripts/setup.sh"
```

This creates `$HOME/.claude/skills/oscilloscope/.venv/` with `pyvisa`, `pyvisa-py`, `pyusb`. macOS also needs `libusb` from Homebrew; the setup script warns if missing (`brew install libusb`).

Run setup if any of these are true: first time on a new machine, you see `ModuleNotFoundError: No module named 'pyvisa'`, or `usb.core.NoBackendError`.

## Tool invocation

```bash
SKILL_DIR="$HOME/.claude/skills/oscilloscope"
PY="$SKILL_DIR/.venv/bin/python $SKILL_DIR/scripts/scope.py"
$PY <subcommand> [args]
```

**Subcommands**: `idn`, `state`, `err`, `selftest`, `beep`, `reset`, `run`, `stop`, `single`, `auto`, `screenshot`, `meas`, `csv`, `deep`, `trig`, `mask`, `math`, `fft`, `label`, `save-setup`, `load-setup`, `save-wmem`, `show-wmem`, `monitor`, `raw`, `recover`. Discover args with `--help`.

Anything not exposed as a subcommand goes through `raw`:

```bash
$PY raw ":TIM:SCAL?"
$PY raw ":TRIG:EDGE:SOUR CHAN2"
```

## Common tasks

### Capture a single triggered screenshot

The reliable pattern. Always do `run` → `single` → wait → `screenshot` because the scope buffers stale data otherwise.

```bash
SKILL_DIR="$HOME/.claude/skills/oscilloscope"
PY="$SKILL_DIR/.venv/bin/python $SKILL_DIR/scripts/scope.py"
TS=$(date +%H%M%S)
OUT_DIR="logs/scope_verification"
mkdir -p "$OUT_DIR"

# Configure trigger (example: CH2 rising edge at 1.65 V)
$PY raw ":TRIG:EDGE:SOUR CHAN2"
$PY raw ":TRIG:EDGE:SLOP POS"
$PY trig edge 2 1.65 POS    # equivalent, handles vendor-specific trigger-level grammar

# Capture
$PY run
sleep 1
$PY single
sleep 2

# Filename extension matches the vendor's native format:
#   Keysight → write .bmp, convert with `sips`
#   RIGOL    → write .png, viewer-ready
$PY screenshot -o "$OUT_DIR/${TS}_label.bmp"   # adjust extension per vendor
```

Why the waits: SCPI commands are queued; `single` returns immediately but the trigger arms asynchronously. Without the 2-second wait, the screenshot may show pre-trigger memory.

### Capture CSV for transition analysis

```bash
$PY csv 1 -o /tmp/ch1.csv
# Header: t_s,v
# Keysight returns 1000 points, RIGOL returns 1200 points (screen depth)
```

If `csv` reports "0 points (no signal captured)", the scope display has nothing on that channel. Verify probe is connected, channel is ON, and trigger is firing (`$PY raw ':TRIG:STAT?'`).

Quick H/L transition extraction (1.65 V threshold for 3.3 V logic):

```bash
awk -F, 'NR==1{next} {
  if ($2 > 1.65) h="H";
  else if ($2 < -1.65) h="-";
  else h="L";
  if (prev != h) {
    printf "t=%.2fµs %s (v=%.2fV)\n", $1*1e6, h, $2;
    prev=h
  }
}' /tmp/ch1.csv
```

### Verify scope state before debugging

```bash
$PY state              # one-line summary of timebase / channels / trigger / sample rate
$PY err                # drain SCPI error queue
$PY raw ":TRIG:STAT?"  # Keysight: AUTO/TRIG'D/STOP/WAIT. RIGOL: TD/WAIT/RUN/AUTO/STOP.
```

If you're chasing a "trigger isn't firing" issue, **always drain `err` first** — a stuck error from a previous session blocks subsequent queries on Keysight.

## SCPI quick reference

Both vendors share most IEEE-488.2 commands; the script absorbs the deltas.

| Setting | Keysight | RIGOL | Notes |
|---|---|---|---|
| Trigger level | `:TRIG:EDGE:LEV <V>,<src>` | `:TRIG:EDGE:LEV <V>` | use `$PY trig edge <ch> <V>` to abstract |
| Timebase offset | `:TIM:POS?` | `:TIM:OFFS?` | wrapped in `$PY state` |
| Channel display | `:CHAN<n>:DISP ON\|OFF` | same | RIGOL accepts `ON/OFF`; Keysight `1/0` |
| Memory depth query | `:ACQ:POIN?` | `:ACQ:MDEPth?` | RIGOL exposes settable depth |
| Waveform mode | `:WAV:POIN:MODE NORM\|RAW` | `:WAV:MODE NORM\|RAW\|MAX` | script picks the right keyword |
| Deep capture | `:DIGitize <ch>` | `:STOP` + `:WAV:MODE RAW` | script branches on vendor |
| FFT | `:FUNC:OPER FFT` (math) | `:MATH:OPER FFT` | dedicated `$PY fft` works on both |
| Math op | `:FUNC:OPER` | `:MATH:OPER` | dedicated `$PY math` works on both |
| Pulse-width trigger | `:TRIG:GLITch:*` | `:TRIG:PULSe:*` | dedicated `$PY trig glitch` works on both |
| Mask test | `:MTESt:*` | `:MASK:*` | only Keysight wired up in the script |
| Screenshot | `:DISP:DATA? BMP, SCR, COL` | `:DISP:DATA? ON,OFF,PNG` | vendor-native, auto-selected |

## Footguns (each has bitten this project)

### 1. Channel display gets disabled by SCPI side-effect (Keysight)

Some `:TRIG:EDGE:SOUR` or `:CHAN:OFFS` commands cause a channel to disappear from the display even though it's still being acquired. If a trace vanishes from screenshots:

```bash
$PY raw ":CHAN1:DISP ON"
$PY raw ":CHAN2:DISP ON"
```

The trace itself wasn't lost — only its display flag.

### 2. BMP not PNG (Keysight)

`screenshot -o foo.png` writes a BMP with `.png` extension that your image viewer probably won't open. On Keysight, always save as `.bmp` and convert:

```bash
sips -s format png foo.bmp --out foo.png    # macOS
convert foo.bmp foo.png                      # Linux ImageMagick
```

On RIGOL, PNG comes back directly — `.png` extension is correct.

### 3. AUTO SCALE button overrides everything

If the scope footer shows `自动定标菜单` (Auto-Scale Menu), the user pressed the front-panel AUTO button and the scope rewrote your timebase / vertical settings. Re-issue your `:TIM:SCAL` / `:CHAN1:SCAL` commands.

### 4. ×100 probe attenuation is read-only (Keysight only)

Don't waste a roundtrip on `:CHAN1:PROB 1`. The probe is AutoProbe-locked at ×100 and the scope already reports post-attenuation volts. On RIGOL, `:CHAN1:PROBe` is settable normally.

### 5. RIGOL USBTMC pipe-stall after heavy commands

After certain SCPI sequences (especially `state` with many queries, then closing the session), the next `scope.py <cmd>` invocation may fail with:

```
struct.error: unpack_from requires a buffer of at least 4 bytes ... (actual buffer size is 2 / 0)
```

The script auto-recovers via USB port reset (`dev.reset()`) on the next open, costing ~1.5 s extra. If recovery itself fails, run:

```bash
$PY recover     # two-stage: INITIATE_CLEAR, then dev.reset() fallback
```

Only power-cycle the scope as a last resort.

### 6. RIGOL binary-block settle delay

After writing a `:WAV:DATA?` or `:DISP:DATA?` (PNG) query, RIGOL needs ~500 ms to queue the bulk-IN response. Without that delay, pyvisa-py's first read returns 0 bytes and the transfer aborts. The script's `queryb()` inserts this delay automatically for `vendor.name == 'rigol'`. Don't bypass it.

### 7. RIGOL deep memory defaults to AUTO = 1200 points

`scope.py deep CHAN1 -n 1000000` will only return 1200 points unless `:ACQ:MDEPth` is bumped first:

```bash
$PY raw ":ACQuire:MDEPth 1200000"      # one of: AUTO, 12000, 120000, 1200000, 12000000, 24000000
$PY deep 1 -n 1000000 -o /tmp/deep.csv  # now returns the requested depth
```

### 8. Stale waveform buffer after stop

If you call `csv` while the scope is in STOP mode after a prior capture, you sometimes get the *previous* trigger event. To force a fresh capture: `$PY run` → wait → `$PY stop` → `$PY csv` immediately, OR rely on `_ensure_acquired` which polls `:TRIG:STATus?` before stopping.

### 9. probe-rs and scope share the USB host but not bus contention

probe-rs (ST-Link) and `scope.py` use independent USB devices — they can run simultaneously without interference. If `scope.py` hangs but ST-Link works, the issue is the scope's USB endpoint, not bus arbitration. `scope.py recover` resets the USB pipe stall without re-opening the VISA session.

## Project-specific defaults (emst-core)

These are useful when probing TIM1 outputs (PA7/PA8) and post-bridge differential output.

**Pre-bridge (PA7/PA8 digital, 0–3.3 V, complementary)**:

```bash
$PY raw ":CHAN1:SCAL 1.0"
$PY raw ":CHAN1:OFFS -0.35"   # CH1 in upper half
$PY raw ":CHAN2:SCAL 1.0"
$PY raw ":CHAN2:OFFS 3.65"    # CH2 in lower half — separates the two traces visually
$PY raw ":TIM:SCAL 5e-6"
$PY raw ":TIM:POS 11.65e-6"    # Keysight; RIGOL uses :TIM:OFFS
```

**Post-bridge (differential output, ±5 V to ±10 V swing)**:

```bash
$PY raw ":CHAN1:SCAL 5.0"
$PY raw ":CHAN1:OFFS 0"
$PY raw ":TIM:SCAL 10e-6"
```

For the 6-waveform suite (SS/SD/DD/NSS/NSD/NDD): trigger CH2 ↑ at 1.65 V works for SS/SD/NSS/NSD (PA7 has rising edges). DD/NDD have one pin stuck — use CH1 ↑ at 1.65 V for NDD (positive output) and CH1 ↓ at -1.5 V for DD (negative output).

## Files

```
~/.claude/skills/oscilloscope/
├── SKILL.md                    ← this file
├── scripts/
│   ├── scope.py                ← the helper (vendor-aware, ~800 lines)
│   ├── requirements.txt        ← pyvisa, pyvisa-py, pyusb
│   └── setup.sh                ← creates .venv/ on first run
└── .venv/                      ← created by setup.sh; gitignore'd elsewhere
```

The skill has no external dependency on `~/Work/scope/`. After `setup.sh` runs once, everything works from the bundled venv.
