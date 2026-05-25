---
name: rs485-modbus
description: Drive a USB-to-RS485 dongle as a Modbus RTU master via the bundled `modbus.py` helper for firmware bring-up, register dump, and protocol-level debugging. Use this skill ANY time the user mentions RS485, Modbus, Modbus RTU, holding/input registers, function codes (FC=03/04/06/10), CRC errors, "波特率", "从机地址", "寄存器", USB-RS485 adapter (FT232/CH340/CP210x), or wants to ping/read/write a Modbus slave from the Mac. Also use when the user says things like "测试 Modbus 通信", "读一下版本号", "发个起始命令", "the slave isn't responding", or is debugging a STM32/embedded firmware that exposes a Modbus interface. Self-contained — bundles the helper script + venv bootstrap; no external Modbus tooling required. Knows the emst-core preset (port `/dev/cu.usbserial-A50285BI`, slave 1, 115200 8N1, register map for VERSION/CMD/LEVEL/STATE).
---

# RS485 Modbus RTU — Operation Skill

Drives a USB-to-RS485 dongle as a Modbus RTU master via a bundled `modbus.py`. Pure pyserial + hand-rolled CRC — no `pymodbus`, no `mbpoll`. Two layers: **generic** (any port/slave/register) and **emst-core preset** (defaults baked in, version readout pretty-printed).

## When this skill applies

- Bringing up Modbus on new hardware
- Verifying a slave is alive (read VERSION / known input register)
- Inspecting holding/input register values
- Sending control commands to a Modbus slave
- Diagnosing CRC errors, no-response timeouts, slave-address mismatches
- Sanity-checking firmware UART/DMA RX path during emst-core bring-up

If the user is sniffing traffic between an existing master/slave pair (passive bus tap), this skill does not yet do that — say so and stop.

## Hardware assumption

USB-to-RS485 dongle with **automatic direction control** (most modern FT232/CH340-based "RS485 USB转串口" adapters). No DE/RE pin handling in software. If the user has a manual-DE adapter, this skill won't work — flag it.

## Bootstrap (first run on a new machine)

```bash
bash "$HOME/.claude/skills/rs485-modbus/scripts/setup.sh"
```

Creates `$HOME/.claude/skills/rs485-modbus/.venv/` with `pyserial`. Idempotent. Run it again any time you see `ModuleNotFoundError: No module named 'serial'`.

## Tool invocation

```bash
SKILL_DIR="$HOME/.claude/skills/rs485-modbus"
PY="$SKILL_DIR/.venv/bin/python $SKILL_DIR/scripts/modbus.py"
$PY <subcommand> [--preset emst-core] [args]
```

Subcommands: `ports`, `ping`, `read`, `write`. Discover args with `<subcommand> --help`.

## Workflow 1 — first contact (always start here)

```bash
$PY ports                       # list /dev/cu.usbserial* / cu.SLAB* / cu.wchusb*
$PY ping --preset emst-core     # FC=04 read VERSION (input regs 0..1)
```

Expected on emst-core: `OK  slave=1  VERSION=1.0.0  reserved=0`.

If `ping` times out, jump to **Failure modes** below before doing anything else. Don't keep retrying — diagnose the layer 1 issue first.

## Workflow 2 — read registers

```bash
# Read 4 holding regs starting at 0x00 (CMD, LEVEL, ...)
$PY read --preset emst-core --type holding --addr 0x00 --count 4

# Read STATE register
$PY read --preset emst-core --type holding --addr 0x20 --count 1

# Generic (no preset)
$PY read --port /dev/cu.usbserial-XXXX --slave 7 --type input --addr 100 --count 8
```

Output is one line per register: `  0x0020: 0x0001  (1)`.

## Workflow 3 — write registers

```bash
# Single holding register (FC=06): set LEVEL = 3
$PY write --preset emst-core --addr 0x01 --values 3

# Multiple holding registers (FC=10): set LEVEL=3 then CMD=START atomically
$PY write --preset emst-core --addr 0x00 --values 1,3
# Note: --addr is the start; --values are written in order.
# emst layout is CMD(0x00), LEVEL(0x01) — so use --addr 0x00 --values <CMD>,<LEVEL>.
```

Always verify with a follow-up `read` of the same range.

## emst-core preset reference

Built into `--preset emst-core`. Source of truth: `src/lib.rs`, `src/rx_parser.rs`.

| Setting | Value |
|---|---|
| Port | `/dev/cu.usbserial-A50285BI` (FT232R) |
| Baud / frame | 115200 8N1 |
| Slave addr | 1 |
| Input  reg 0x00..0x01 | VERSION (4 bytes: major.minor.patch.reserved) |
| Holding reg 0x00 | CMD: 0=NOOP, 1=START, 2=SWITCH_LEVEL, 3=STOP |
| Holding reg 0x01 | LEVEL (treatment intensity) |
| Holding reg 0x20 | STATE readback |

Atomic START sequence (host writes LEVEL then CMD in one FC=10 burst):

```bash
$PY write --preset emst-core --addr 0x00 --values 1,3   # CMD=START LEVEL=3
$PY read  --preset emst-core --type holding --addr 0x20 --count 1   # confirm state moved
```

## Failure modes (diagnosis order)

When `ping` fails, work through these in order — don't shotgun:

1. **No port enumerated** (`ports` lists nothing matching `/dev/cu.usbserial*`)
   - Adapter not plugged, or driver missing. Check `ioreg -p IOUSB -l | grep "USB Product Name"`. CH340 clones may need WCH driver; FT232/CP210x are usually built-in on macOS 14+.

2. **Port present, but `Permission denied`**
   - Another process holds the port. `lsof | grep usbserial` to find it. Close minicom/screen/serial monitors.

3. **Timeout (no bytes received at all)**
   - Wiring: A/B swapped is the #1 cause. Try swapping the two RS485 lines.
   - Slave address wrong: the firmware expects 1 by default; verify `MODBUS_ADDR` in `src/lib.rs`.
   - Baud mismatch: confirm `BAUD_RATE` in firmware matches `--baud`.
   - Firmware not running / not listening: check defmt-rtt output for "waiting modbus command...".
   - Adapter without auto-direction: TX works but RX never enables. Confirm adapter is auto-DE.

4. **CRC mismatch on response**
   - EMI / long unterminated bus: add 120 Ω termination across A/B at one end.
   - Floating bus when idle: add bias resistors (commonly built into the dongle, but cheap clones omit them).
   - Baud slightly off: try ±1% — STM32 internal RC vs HSE difference can bite.

5. **Wrong slave address echoed back**
   - Multi-drop bus with another device responding. Disconnect others or change slave addresses.

6. **Modbus exception code in response** (e.g. `MBException: code=2 ILLEGAL_DATA_ADDRESS`)
   - You read a register the slave doesn't map. Check `set_inputs_from_u8` / register layout in firmware.

## Notes

- All numeric args accept `0x`-prefixed hex or decimal.
- `--values` for `write` is comma-separated, also accepts hex per item: `--values 0x0001,0x0003`.
- Default request timeout is 300 ms; bump with `--timeout 1.0` for slow slaves.
- The script does **not** retry on its own — keep failures loud so you actually look at the bus.
