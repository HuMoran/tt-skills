# Changelog

All notable changes to tt-skills.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-08-18

### Added

- `creo-jlink` skill — runs automation inside PTC Creo Parametric through J-Link (the Java API): batch STEP/BOM/drawing export, mass and material properties. Ships `creo-run.bat`, which validates the environment, generates the `config.pro` / `protk.dat` / message files Creo needs, compiles with `javac --release 21` and launches Creo with the correct JVM in front. Also ships `HelloJlink.java` as a smoke test and reflection skeleton.
- `creo-remote` skill — drives Creo on a remote Windows box from macOS/Linux over SSH. Pushes the app and `creo-run.bat` (with the local config patched in), compiles, delivers the launch into the logged-on interactive session via `schtasks /it`, polls for a done marker, then frees the license seat. Reaches Windows through WSL + interop rather than PowerShell over SSH, which avoids `-EncodedCommand` and console-codepage mangling entirely.
- `creo-run.bat --check` reads the bytecode version of **every** class in `otk.jar` and takes the maximum before comparing against the configured JDK. Creo 13.4's `otk.jar` mixes Java 7 and Java 25 classes, so sampling a single entry reports Java 7 and passes a JDK that cannot load the jar — and the resulting failure is silent: Creo starts normally and the app never runs.

### Changed

- Plugin scope widened from "hardware bring-up" to "hardware and CAD bring-up".

## [0.2.0] - 2026-05-25

### Added

- `rs485-modbus` skill — drives a USB-to-RS485 dongle (FT232/CH340/CP210x) as a Modbus RTU master via pure `pyserial` + hand-rolled CRC. No `pymodbus`, no `mbpoll` dependency. Supports holding/input register reads, single + multi register writes (FC 03/04/06/10), CRC error and timeout diagnostics. Ships a self-contained `modbus.py` plus an `emst-core` preset for the project-specific bus.

### Changed

- Bump plugin description and keywords to reflect the multi-skill scope (hardware bring-up).

## [0.1.0] - 2026-05-25

### Added

- Initial release.
- `oscilloscope` skill — drives Keysight DSO5000-series (tested DSO5012A fw 06.10) and RIGOL DS1000Z-series (tested DS1102Z-E fw 00.06.02) over VISA / USBTMC. Auto-detects vendor by `*IDN?`. Subcommands: `idn / state / err / selftest / beep / reset / run / stop / single / auto / screenshot / csv / deep / meas / trig / mask / math / fft / label / save-setup / load-setup / save-wmem / show-wmem / monitor / raw / recover`.
- `.claude-plugin/` manifests so the repo doubles as a Claude Code plugin marketplace.
