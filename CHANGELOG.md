# Changelog

All notable changes to tt-skills.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-25

### Added

- Initial release.
- `oscilloscope` skill — drives Keysight DSO5000-series (tested DSO5012A fw 06.10) and RIGOL DS1000Z-series (tested DS1102Z-E fw 00.06.02) over VISA / USBTMC. Auto-detects vendor by `*IDN?`. Subcommands: `idn / state / err / selftest / beep / reset / run / stop / single / auto / screenshot / csv / deep / meas / trig / mask / math / fft / label / save-setup / load-setup / save-wmem / show-wmem / monitor / raw / recover`.
- `.claude-plugin/` manifests so the repo doubles as a Claude Code plugin marketplace.
