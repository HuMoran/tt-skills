#!/usr/bin/env python3
"""Multi-vendor benchtop oscilloscope control via VISA / USBTMC.

    scope.py <subcommand> [options]

Supported scopes (auto-detected via ``*IDN?``):
  - Keysight / Agilent InfiniiVision DSO5000-series (tested: DSO5012A, fw 06.10)
  - RIGOL DS1000Z-series (tested: DS1102Z-E, fw 00.06.02)

Run ``scope.py -h`` or ``scope.py <sub> -h`` for full help.
"""
from __future__ import annotations
import argparse
import datetime as dt
import pathlib
import re
import sys
import time

import pyvisa
import usb.core
import usb.util

# Known-good addresses. Kept as hints — the default behavior is auto-discover.
KEYSIGHT_DSO5012A_DEFAULT = 'USB0::2391::5987::MY50340343::0::INSTR'
RIGOL_DS1102ZE_DEFAULT    = 'USB0::6833::1303::DS1ZE224209419::0::INSTR'

DEFAULT_TIMEOUT_MS = 10_000
SCREENSHOT_TIMEOUT_MS = 30_000
DEEP_TIMEOUT_MS = 120_000

# Common analog measurements. Both vendors accept the :MEASure:<KEY>? CHAN<n>
# form for these. Vendor-specific extras can be added later if needed.
MEASUREMENTS = [
    ('VPP',    'Vpp',       'V'),
    ('VMAX',   'Vmax',      'V'),
    ('VMIN',   'Vmin',      'V'),
    ('VAMP',   'Vamp',      'V'),
    ('VAVerage', 'Vavg',    'V'),
    ('VRMS',   'Vrms',      'V'),
    ('VTOP',   'Vtop',      'V'),
    ('VBASe',  'Vbase',     'V'),
    ('FREQuency', 'Freq',   'Hz'),
    ('PERiod', 'Period',    's'),
    ('RISetime', 'Rise',    's'),
    ('FALLtime', 'Fall',    's'),
    ('PWIDth', 'PosWidth',  's'),
    ('NWIDth', 'NegWidth',  's'),
    ('DUTYcycle', 'Duty',   '%'),
    ('OVERshoot', 'Overshoot', '%'),
    ('PREShoot', 'Preshoot', '%'),
]
NOSIG = 9.9e37  # Keysight/RIGOL "no signal" sentinel


# ---------- vendor adapter ----------

class Vendor:
    """Per-vendor capability bundle. Populated from *IDN?."""
    def __init__(self, idn: str):
        self.idn = idn
        u = idn.upper()
        if 'KEYSIGHT' in u or 'AGILENT' in u:
            self.name = 'keysight'
            self.model = 'DSO5000-series'
            # :DISP:DATA? returns IEEE binary block prefixed BMP image on fw 06.10.
            self.screenshot_query = ':DISPlay:DATA? BMP, SCReen, COLor'
            self.screenshot_ext = 'bmp'
            self.probe_lockable = True   # AutoProbe locks attenuation; don't try to set
            self.trig_level_takes_source = True   # :TRIG:EDGE:LEV <V>,<src>
            self.wav_mode_cmd = ':WAVeform:POINts:MODE'   # NORMal | RAW | MAXimum
            self.deep_via_digitize = True   # :DIGitize then read with POINts MAXimum
            self.channels = (1, 2)
            self.has_mtest = True
            self.has_wmem = True     # :WMEMory<n>
        elif 'RIGOL' in u:
            self.name = 'rigol'
            # DS1000Z covers DS1054Z/DS1074Z/DS1102Z-E/DS1104Z. DS1102Z-E is 2-ch.
            self.model = 'DS1000Z'
            # PNG comes back as a 1152-byte IEEE block prefix + PNG; PyVISA strips the prefix.
            self.screenshot_query = ':DISPlay:DATA? ON,OFF,PNG'
            self.screenshot_ext = 'png'
            self.probe_lockable = False
            self.trig_level_takes_source = False  # :TRIG:EDGe:LEVel <V>
            self.wav_mode_cmd = ':WAVeform:MODE'   # NORMal | RAW | MAXimum
            self.deep_via_digitize = False  # :STOP then :WAV:MODE RAW + chunked reads
            # DS1102Z-E is 2-channel; DS1054Z is 4-channel. Probe display? to find out.
            self.channels = (1, 2, 3, 4)
            self.has_mtest = False  # SCPI subtree is different (:MASK:), not wired up
            self.has_wmem = False   # uses :REFerence<n>:, not implemented yet
            # DS1000Z idn ends with ",<serial>,<fw>". Detect 2-ch DS1102Z-E to trim channels.
            if 'DS1102Z-E' in u:
                self.channels = (1, 2)
        else:
            self.name = 'unknown'
            self.model = 'unknown'
            self.screenshot_query = ':DISPlay:DATA?'
            self.screenshot_ext = 'bin'
            self.probe_lockable = False
            self.trig_level_takes_source = False
            self.wav_mode_cmd = ':WAVeform:MODE'
            self.deep_via_digitize = False
            self.channels = (1, 2)
            self.has_mtest = False
            self.has_wmem = False

    def __repr__(self):
        return f'Vendor({self.name}, {self.model})'


# ---------- channel parsing ----------

def parse_chan(raw: str) -> str:
    """Normalize channel arg to CHAN1..CHAN4 / FUNC / MATH / WMEM<n>."""
    s = raw.strip().upper()
    if re.fullmatch(r'[1-4]|CH[1-4]|CHAN[1-4]', s):
        n = s[-1]
        return f'CHAN{n}'
    if s in ('FUNC', 'MATH', 'F'):
        return 'FUNC'
    if re.fullmatch(r'WMEM[1-4]', s):
        return s
    if re.fullmatch(r'WM[1-4]', s):
        return 'WMEM' + s[-1]
    if re.fullmatch(r'REF[1-4]', s):
        return s
    raise argparse.ArgumentTypeError(f'unknown channel: {raw!r}')


# ---------- address discovery ----------

def discover_addr(prefer: str | None = None) -> str:
    """Find a USB scope on the bus.

    If ``prefer`` matches an enumerable resource, use it. Else pick the first
    USB::*::INSTR. Raises SystemExit with a clear message if nothing's found.
    """
    rm = pyvisa.ResourceManager('@py')
    resources = [r for r in rm.list_resources() if r.startswith('USB')]
    if prefer and prefer in rm.list_resources():
        return prefer
    if not resources:
        print('no USB VISA scope found on the bus', file=sys.stderr)
        print('  - check the USB cable is connected and the scope is powered on', file=sys.stderr)
        print('  - try: scope.py recover', file=sys.stderr)
        sys.exit(3)
    if len(resources) > 1:
        print(f'multiple USB scopes found, picking first: {resources[0]}', file=sys.stderr)
        for r in resources[1:]:
            print(f'  (also seen: {r})', file=sys.stderr)
    return resources[0]


# ---------- Scope wrapper ----------

class Scope:
    def __init__(self, addr: str | None = None, timeout=DEFAULT_TIMEOUT_MS):
        self.addr = addr or discover_addr()
        self.rm = pyvisa.ResourceManager('@py')
        # First-attempt open. Use a short timeout so a stale-state device fails
        # fast and we can escalate to a USB reset.
        idn = self._open_and_idn(probe_timeout=2500)
        if idn is None:
            # Escalation: pyvisa-py reads a 2-byte stub from a desynced RIGOL
            # USBTMC state machine and raises struct.error / VisaIOError.
            # A USB port reset (~1.5 s) emulates a replug and fully resyncs.
            _usb_reset(self.addr)
            idn = self._open_and_idn(probe_timeout=3500)
            if idn is None:
                raise RuntimeError(
                    f'cannot get *IDN? from {self.addr} even after USB reset; '
                    f'try `scope.py recover` or power-cycle the scope')
        self.dso.timeout = timeout
        self.vendor = Vendor(idn)

    def _open_and_idn(self, probe_timeout: int) -> str | None:
        """Open the VISA session and probe *IDN?. Returns IDN, or None on stale state."""
        try:
            self.dso = self.rm.open_resource(self.addr, timeout=probe_timeout)
            self.dso.chunk_size = 1024 * 1024
            return self.dso.query('*IDN?').strip()
        except Exception:
            try: self.dso.close()
            except Exception: pass
            return None

    def write(self, cmd):  self.dso.write(cmd)
    def query(self, cmd):  return self.dso.query(cmd).strip()
    def queryb(self, cmd, datatype='B'):
        # pyvisa's query_binary_values() works on Keysight but trips on RIGOL
        # DS1000Z (its IEEE-block tail handling diverges between vendors and
        # pyvisa-py's USBTMC layer is finicky). Use read_raw + manual parse —
        # robust on both, with no change to caller semantics (returns bytes).
        del datatype  # always BYTE for our use cases
        self.write(cmd)
        # RIGOL needs a settle window after a binary-block query — otherwise
        # pyvisa-py's first bulk-IN read returns 0 bytes and the whole transfer
        # aborts. ~500 ms covers both the 1.2 kB waveform read and the 30 kB
        # PNG render. Keysight ignores this path (uses its own pyvisa path).
        if getattr(self, 'vendor', None) and self.vendor.name == 'rigol':
            time.sleep(0.5)
        return self._read_ieee_block()

    def _read_ieee_block(self) -> bytes:
        """Read an IEEE-488.2 definite-length block: ``#<n><nnn...n><payload>``."""
        first = self.dso.read_raw()
        if not first or first[:1] != b'#':
            raise RuntimeError(f'expected IEEE block, got {first[:8]!r}')
        nlen = int(first[1:2])
        size = int(first[2:2 + nlen])
        payload = first[2 + nlen:]
        # Strip optional trailing terminator (\n or \r\n)
        while len(payload) < size:
            payload += self.dso.read_raw()
        return bytes(payload[:size])

    def drain_errors(self):
        errs = []
        for _ in range(32):
            e = self.query(':SYSTem:ERRor?')
            errs.append(e)
            # Keysight: "+0,...". RIGOL: "0,..." (no leading +).
            if e.startswith('+0,') or e.startswith('0,'):
                break
        return errs

    def set_timeout(self, ms):
        self.dso.timeout = ms

    def close(self):
        try: self.dso.close()
        except Exception: pass


def _usb_reset(addr: str):
    """Soft replug — issues USB port reset on the device. ~1.5s.

    Use as last-resort recovery; INITIATE_CLEAR is faster but doesn't resync
    RIGOL DS1000Z's USBTMC state machine after certain command sequences.
    """
    m = re.search(r'USB0?::(\d+)::(\d+)::', addr)
    if not m:
        return
    vid, pid = int(m.group(1)), int(m.group(2))
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        return
    try: dev.reset()
    except Exception: pass
    usb.util.dispose_resources(dev)
    time.sleep(1.5)


def _is_no_error(line: str) -> bool:
    return line.startswith('+0,') or line.startswith('0,')


# ---------- basic ops ----------

def cmd_idn(s: Scope, _):
    print(s.query('*IDN?'))


def _safe_query(s: Scope, cmd: str, default='?') -> str:
    """Query that drains the error queue on failure instead of poisoning the session."""
    try:
        return s.query(cmd)
    except Exception:
        try: s.drain_errors()
        except Exception: pass
        return default


def cmd_state(s: Scope, _):
    # Different vendors put the timebase offset under different keywords:
    # Keysight = :TIMebase:POSition, RIGOL = :TIMebase:OFFSet (alias :TIMebase:MAIN:OFFSet).
    offset_cmd = ':TIMebase:OFFSet?' if s.vendor.name == 'rigol' else ':TIMebase:POSition?'
    print(f'{"IDN":10}', s.vendor.idn)
    print(f'{"Vendor":10}', s.vendor.name, '/', s.vendor.model)
    print(f'{"Timebase":10}', _safe_query(s, ':TIMebase:SCALe?'), 's/div')
    print(f'{"Position":10}', _safe_query(s, offset_cmd), 's')
    for ch in s.vendor.channels:
        try:
            on = s.query(f':CHANnel{ch}:DISPlay?') in ('1', 'ON')
            sc = s.query(f':CHANnel{ch}:SCALe?')
            of = s.query(f':CHANnel{ch}:OFFSet?')
            cp = s.query(f':CHANnel{ch}:COUPling?')
            pr = s.query(f':CHANnel{ch}:PROBe?')
            print(f'CH{ch:<8} {sc} V/div  {of} V offset  {cp}  probe×{pr}  {"ON" if on else "off"}')
        except Exception:
            # RIGOL probes a 4-ch grammar even on the 2-ch DS1102Z-E; tolerate missing CHs.
            s.drain_errors()
    # :TRIG:MODE? semantics differ: Keysight returns sweep (NORM/AUTO), RIGOL returns
    # trigger type (EDGE/PULSe/...). Display whichever is meaningful for the vendor.
    tmode = _safe_query(s, ':TRIGger:MODE?')
    trig_lev = _safe_query(s, ':TRIGger:EDGE:LEVel?')
    trig_src = _safe_query(s, ':TRIGger:EDGE:SOURce?')
    trig_slope = _safe_query(s, ':TRIGger:EDGE:SLOPe?')
    sweep = _safe_query(s, ':TRIGger:SWEep?')
    print(f'{"Trigger":10} {tmode} {trig_src} @ {trig_lev} V {trig_slope} | sweep {sweep}')
    if s.vendor.name == 'rigol':
        print(f'{"MemDepth":10}', _safe_query(s, ':ACQuire:MDEPth?'))
    print(f'{"SampRate":10}', _safe_query(s, ':ACQuire:SRATe?'), 'Sa/s')
    print(f'{"AcqType":10}', _safe_query(s, ':ACQuire:TYPE?'))
    # :WAVeform:POINts? wants :WAV:SOURce context; set one first
    try:
        s.write(':WAVeform:SOURce CHAN1')
        print(f'{"Points":10}', s.query(':WAVeform:POINts?'))
    except Exception:
        s.drain_errors()


def cmd_err(s: Scope, _):
    for e in s.drain_errors():
        print(e)


def cmd_selftest(s: Scope, _):
    s.set_timeout(30_000)
    r = s.query('*TST?')
    print('*TST? =', r, '(0 = pass)')


def cmd_beep(s: Scope, _):
    if s.vendor.name == 'rigol':
        # DS1000Z has no fire-now beep; toggle the beeper on then back to prior state.
        prev = s.query(':SYSTem:BEEPer?')
        s.write(':SYSTem:BEEPer ON')
        time.sleep(0.2)
        s.write(f':SYSTem:BEEPer {prev}')
        print('beep (rigol toggle)')
    else:
        s.write(':SYSTem:BEEPer')
        print('beep')


def cmd_reset(s: Scope, _):
    s.write('*RST')
    print('*RST sent')


def _acq(s, op):
    s.write(op)
    print(op.strip(':').split()[0])

def cmd_run(s, _):    _acq(s, ':RUN')
def cmd_stop(s, _):   _acq(s, ':STOP')
def cmd_single(s, _): _acq(s, ':SINGle')
def cmd_auto(s, _):
    # Keysight: :AUToscale.  RIGOL: :AUToscale (both ok).
    _acq(s, ':AUToscale')


# ---------- screenshot ----------

def cmd_screenshot(s: Scope, args):
    s.set_timeout(SCREENSHOT_TIMEOUT_MS)
    ext = s.vendor.screenshot_ext
    out = pathlib.Path(args.out or f'scope_{dt.datetime.now():%Y%m%d_%H%M%S}.{ext}')
    blob = s.queryb(s.vendor.screenshot_query)
    out.write_bytes(blob)
    fmt = ext.upper()
    print(f'wrote {out} ({len(blob)} bytes, {fmt})')
    if s.vendor.name == 'keysight':
        # Old DSO5012A fw only outputs BMP; remind the caller how to convert.
        if out.suffix.lower() == '.png':
            print('  WARN: extension is .png but Keysight fw outputs BMP — viewer may reject',
                  file=sys.stderr)
        else:
            print(f'  (to PNG: sips -s format png {out} --out {out.with_suffix(".png")})')


# ---------- measurements ----------

def _fmt_value(raw, unit):
    try:
        v = float(raw)
    except ValueError:
        return raw
    if abs(v) > 1e36:
        return 'no-signal'
    return f'{v:.6g} {unit}'

def cmd_meas(s: Scope, args):
    chan = parse_chan(args.channel)
    if args.list:
        for key, label, unit in MEASUREMENTS:
            print(f'  {label:10} :MEAS:{key}? {chan} ({unit})')
        return
    if args.one:
        key = args.one.upper()
        print(_fmt_value(s.query(f':MEASure:{key}? {chan}'),
                         next((u for k, _, u in MEASUREMENTS if k.startswith(key)), '')))
        return
    for key, label, unit in MEASUREMENTS:
        try:
            r = s.query(f':MEASure:{key}? {chan}')
            print(f'  {label:10} {_fmt_value(r, unit)}')
        except Exception:
            # Drain so one stuck query doesn't poison the rest.
            s.drain_errors()
            print(f'  {label:10} (error)')


# ---------- waveform capture ----------

def _ensure_acquired(s: Scope, chan, timeout_ms=15_000):
    """Freeze a snapshot so :WAV:DATA? has data to return.

    Keysight: :DIGitize (with AUTO sweep to avoid hangs on missed trigger).
    RIGOL:    poll :TRIG:STATus? until acquisition completes (AUTO/TD), then :STOP.
              Polling is more reliable than ``:RUN + sleep + :STOP`` because the
              sleep races with AUTO-sweep restart and you end up STOPing mid-frame.
    """
    old_to = s.dso.timeout
    s.set_timeout(timeout_ms)
    try:
        prev_sweep = s.query(':TRIGger:SWEep?')
    except Exception:
        prev_sweep = 'AUTO'
    s.write(':TRIGger:SWEep AUTO')
    if s.vendor.deep_via_digitize:
        s.write(f':DIGitize {chan}')
        s.query('*OPC?')
    else:
        # RIGOL: kick off acquisition, wait for trigger/auto-sweep, then freeze.
        s.write(':RUN')
        deadline = time.time() + min(timeout_ms / 1000.0, 5.0)
        while time.time() < deadline:
            try:
                stat = s.query(':TRIGger:STATus?')
            except Exception:
                s.drain_errors(); break
            # TD = trigger'd / STOP = single-shot done / AUTO = auto-swept frame ready
            if stat in ('TD', 'STOP', 'AUTO'):
                break
            time.sleep(0.1)
        s.write(':STOP')
        # Brief settle so STAT actually transitions to STOP before next query.
        time.sleep(0.1)
    if prev_sweep and prev_sweep != 'AUTO':
        s.write(f':TRIGger:SWEep {prev_sweep}')
    s.set_timeout(old_to)


def _read_preamble(s):
    """:WAV:PRE? → (fmt, type, points, count, xinc, xorg, xref, yinc, yorg, yref)

    Both Keysight and RIGOL return the same 10-field IEEE preamble.
    """
    pre = s.query(':WAVeform:PREamble?').split(',')
    return dict(
        fmt=int(pre[0]), type=int(pre[1]), points=int(pre[2]), count=int(pre[3]),
        xinc=float(pre[4]), xorg=float(pre[5]), xref=int(pre[6]),
        yinc=float(pre[7]), yorg=float(pre[8]), yref=int(pre[9]),
    )


def _set_wav_mode(s: Scope, mode: str):
    """Pick the right :WAV:*MODE command for the vendor."""
    s.write(f'{s.vendor.wav_mode_cmd} {mode}')


def _dump_waveform(s: Scope, chan, out, mode='NORMal', npoints=None):
    s.write(f':WAVeform:SOURce {chan}')
    _set_wav_mode(s, mode)
    if npoints is not None:
        s.write(f':WAVeform:POINts {npoints}')
    elif mode == 'RAW':
        s.write(':WAVeform:POINts MAXimum')
    s.write(':WAVeform:FORMat BYTE')
    p = _read_preamble(s)
    # RIGOL with RAW mode + huge memory requires chunked reads; ~250k samples/call.
    if s.vendor.name == 'rigol' and mode == 'RAW' and (npoints or 0) > 250_000:
        data = _rigol_chunked_read(s, int(p['points']))
    else:
        data = s.queryb(':WAVeform:DATA?')
    n: int = int(min(len(data), p['points']))
    with open(out, 'w') as f:
        f.write('t_s,v\n')
        for i in range(n):
            t = p['xorg'] + (i - p['xref']) * p['xinc']
            v = (data[i] - p['yref']) * p['yinc'] + p['yorg']
            f.write(f'{t:.9e},{v:.6e}\n')
    return n, p


def _rigol_chunked_read(s: Scope, total: int, chunk=250_000) -> bytes:
    """DS1000Z USBTMC tops out around 250k samples per :WAV:DATA? call.

    Use :WAV:STARt / :WAV:STOP to window the memory in chunks.
    """
    buf = bytearray()
    pos = 1
    while pos <= total:
        end = min(pos + chunk - 1, total)
        s.write(f':WAVeform:STARt {pos}')
        s.write(f':WAVeform:STOP {end}')
        buf += s.queryb(':WAVeform:DATA?')
        pos = end + 1
    return bytes(buf)


def cmd_csv(s: Scope, args):
    chan = parse_chan(args.channel)
    out = pathlib.Path(args.out or f'{chan}_{dt.datetime.now():%Y%m%d_%H%M%S}.csv')
    _ensure_acquired(s, chan)
    # Force screen-depth (1200 on RIGOL, 1000 on Keysight); a prior deep run may
    # have left POINts at MAXimum, which can trigger a multi-MB read that hangs USBTMC.
    screen_pts = 1200 if s.vendor.name == 'rigol' else 1000
    n, p = _dump_waveform(s, chan, out, mode='NORMal', npoints=screen_pts)
    if n == 0 or p['xinc'] == 0.0:
        print(f'wrote {out}: 0 points (no signal captured on {chan})', file=sys.stderr)
        print(f'  Check: scope is RUN+triggered, {chan} display is ON, probe sees a signal',
              file=sys.stderr)
        sys.exit(2)
    print(f'wrote {out}: {n} points, Δt={p["xinc"]:.3e}s (fs={1/p["xinc"]:.3e} Sa/s)')


def cmd_deep(s: Scope, args):
    chan = parse_chan(args.channel)
    out = pathlib.Path(args.out or f'{chan}_deep_{dt.datetime.now():%Y%m%d_%H%M%S}.csv')
    s.set_timeout(DEEP_TIMEOUT_MS)
    print(f'capturing {chan} deep memory...')
    _ensure_acquired(s, chan, timeout_ms=DEEP_TIMEOUT_MS)
    try:
        acq_points = s.query(':ACQuire:POINts?')
    except Exception:
        s.drain_errors()
        acq_points = s.query(':ACQuire:MDEPth?') if s.vendor.name == 'rigol' else '?'
    print(f'acquired {acq_points} samples in memory; requesting {args.points}')
    n, p = _dump_waveform(s, chan, out, mode='RAW', npoints=args.points)
    print(f'wrote {out}: {n} points, Δt={p["xinc"]:.3e}s, window={n*p["xinc"]*1e3:.3f} ms')


# ---------- trigger ----------

def cmd_trig_state(s: Scope, _):
    if s.vendor.name == 'rigol':
        # DS1000Z exposes :TRIG:STATus? directly: TD | WAIT | RUN | AUTO | STOP.
        try:
            print('TRIG:STATus =', s.query(':TRIGger:STATus?'))
        except Exception:
            s.drain_errors()
        return
    cond = int(s.query(':OPERegister:CONDition?') or '0')
    wait_trig = bool(cond & (1 << 3))
    wait_arm  = bool(cond & (1 << 5))
    print(f'OPER cond = {cond} (bit3 wait-trig={wait_trig}, bit5 wait-arm={wait_arm})')


def cmd_trig_edge(s: Scope, args):
    chan = parse_chan(args.channel)
    slope = {'POS': 'POSitive', 'NEG': 'NEGative', 'EITH': 'EITHer'}[args.slope.upper()[:4]]
    s.write(':TRIGger:MODE EDGE')
    s.write(f':TRIGger:EDGE:SOURce {chan}')
    s.write(f':TRIGger:EDGE:SLOPe {slope}')
    if s.vendor.trig_level_takes_source:
        s.write(f':TRIGger:EDGE:LEVel {args.level},{chan}')
    else:
        s.write(f':TRIGger:EDGE:LEVel {args.level}')
    if args.sweep:
        s.write(f':TRIGger:SWEep {args.sweep.upper()}')
    print(f'trigger EDGE {chan} {slope} @ {args.level} V')


def cmd_trig_glitch(s: Scope, args):
    chan = parse_chan(args.channel)
    pol = 'POSitive' if args.polarity.lower().startswith('pos') else 'NEGative'
    if s.vendor.name == 'rigol':
        # DS1000Z calls it PULSe trigger, not GLITch.
        s.write(':TRIGger:MODE PULSe')
        s.write(f':TRIGger:PULSe:SOURce {chan}')
        s.write(f':TRIGger:PULSe:POLarity {pol}')
        # RIGOL qualifier: WHEN { PGReater | PLESs | NGReater | NLESs | PGLess | NGLess }
        sign = 'P' if pol == 'POSitive' else 'N'
        qmap = {'GREaterthan': f'{sign}GReater', 'LESSthan': f'{sign}LESs', 'RANGe': f'{sign}GLess',
                'GRE': f'{sign}GReater', 'LESS': f'{sign}LESs', 'RANG': f'{sign}GLess'}
        s.write(f':TRIGger:PULSe:WHEN {qmap[args.qual]}')
        s.write(f':TRIGger:PULSe:WIDTh {args.t1}')
        if args.t2 is not None:
            s.write(f':TRIGger:PULSe:UWIDth {args.t2}')
        print(f'trigger PULSe {chan} {pol} {args.qual} w={args.t1}'
              + (f' uw={args.t2}' if args.t2 else ''))
        return
    # Keysight path
    s.write(':TRIGger:MODE GLITch')
    s.write(f':TRIGger:GLITch:SOURce {chan}')
    s.write(f':TRIGger:GLITch:POLarity {pol}')
    s.write(f':TRIGger:GLITch:QUALifier {args.qual.upper()}')
    if args.qual.upper() == 'RANGe':
        s.write(f':TRIGger:GLITch:RANGe {args.t2},{args.t1}')
    elif args.qual.upper().startswith('GRE'):
        s.write(f':TRIGger:GLITch:GREaterthan {args.t1}')
    else:
        s.write(f':TRIGger:GLITch:LESSthan {args.t1}')
    print(f'trigger GLITch {chan} {pol} {args.qual} t1={args.t1}' +
          (f' t2={args.t2}' if args.t2 else ''))


# ---------- mask test (Keysight only) ----------

def _require_mtest(s: Scope) -> bool:
    if not s.vendor.has_mtest:
        print(f'mask test not implemented for {s.vendor.name} ({s.vendor.model}) yet.',
              file=sys.stderr)
        print('RIGOL DS1000Z uses :MASK:* SCPI — see programming manual.', file=sys.stderr)
        return False
    return True


def cmd_mask_create(s: Scope, args):
    if not _require_mtest(s): sys.exit(2)
    chan = parse_chan(args.channel)
    s.write(f':MTESt:SOURce {chan}')
    s.write(f':MTESt:AMASk:XDELta {args.xdel}')
    s.write(f':MTESt:AMASk:YDELta {args.ydel}')
    s.write(f':MTESt:AMASk:SOURce {chan}')
    s.write(':MTESt:AMASk:CREate')
    s.write(':MTESt:ENABle 1')
    errs = [e for e in s.drain_errors() if not _is_no_error(e)]
    if errs:
        print('AMASk creation rejected:')
        for e in errs: print(' ', e)
        print('\nFallback: on old firmware AMASk often needs a stopped waveform and')
        print('the MASK test option with FPGA support. Try this from the front panel:')
        print('  1. Acquire a "golden" waveform, then press [Stop]')
        print('  2. [Utility] → Mask Test → Automask (set XΔ/YΔ sliders)')
        print('  3. Save mask to USB as gold.msk')
        print('  4. Use `scope.py mask load /path/gold.msk` + `mask run` from the CLI')
        return
    print(f'mask created from {chan}: XΔ={args.xdel} YΔ={args.ydel}')


def cmd_mask_load(s: Scope, args):
    if not _require_mtest(s): sys.exit(2)
    s.write(f':MTESt:DATA:LOAD "{args.path}"')
    errs = [e for e in s.drain_errors() if not _is_no_error(e)]
    if errs:
        for e in errs: print('ERR', e)
    else:
        print(f'mask loaded: {args.path}')


def cmd_mask_run(s: Scope, args):
    if not _require_mtest(s): sys.exit(2)
    s.write(':MTESt:ENABle 1')
    s.write(':MTESt:RMODe FORever' if not args.count else f':MTESt:RMODe WAVeforms')
    if args.count:
        s.write(f':MTESt:RMODe:WAVeforms {args.count}')
    s.write(':MTESt:RUN')
    if args.count:
        s.set_timeout(max(args.count * 200, 5000))
        while True:
            stat = s.query(':MTESt:RMODe:WAVeforms:REMaining?')
            if stat in ('0', '+0'):
                break
            time.sleep(0.2)
    cmd_mask_stats(s, args)


def cmd_mask_stats(s: Scope, _):
    if not _require_mtest(s): sys.exit(2)
    waves = s.query(':MTESt:COUNt:WAVeforms?')
    fails = s.query(':MTESt:COUNt:FWAVeforms?')
    print(f'waveforms={waves}  failed={fails}')
    if fails.lstrip('+') not in ('0', '0.0', '+0'):
        sys.exit(1)


def cmd_mask_off(s: Scope, _):
    if not _require_mtest(s): sys.exit(2)
    s.write(':MTESt:ENABle 0')
    print('mask disabled')


# ---------- math / fft ----------

def cmd_math(s: Scope, args):
    op = args.op.upper()
    if s.vendor.name == 'rigol':
        # DS1000Z math subsystem
        s.write(f':MATH:OPERator {op}')
        s.write(f':MATH:SOURce1 {parse_chan(args.src1)}')
        if args.src2:
            s.write(f':MATH:SOURce2 {parse_chan(args.src2)}')
        s.write(':MATH:DISPlay ON')
    else:
        s.write(f':FUNCtion:OPERation {op}')
        s.write(f':FUNCtion:SOURce1 {parse_chan(args.src1)}')
        if args.src2:
            s.write(f':FUNCtion:SOURce2 {parse_chan(args.src2)}')
        s.write(':FUNCtion:DISPlay 1')
    print(f'math {op} src1={args.src1}' + (f' src2={args.src2}' if args.src2 else ''))


def cmd_fft(s: Scope, args):
    chan = parse_chan(args.channel)
    if s.vendor.name == 'rigol':
        s.write(':MATH:OPERator FFT')
        s.write(f':MATH:SOURce1 {chan}')
        s.write(':MATH:DISPlay ON')
        if args.window:
            wmap = {'HANN': 'HANNing', 'RECT': 'RECTangle', 'FLAT': 'FLATtop', 'BHAR': 'BLACkman'}
            w = wmap.get(args.window.upper(), args.window.upper())
            try: s.write(f':MATH:FFT:WINDow {w}')
            except Exception: s.drain_errors()
        if args.span is not None:
            try: s.write(f':MATH:FFT:HSCale {args.span}')
            except Exception: s.drain_errors()
        if args.center is not None:
            try: s.write(f':MATH:FFT:HCENter {args.center}')
            except Exception: s.drain_errors()
        print(f'FFT active on {chan} (rigol)')
        return
    # Keysight: FFT is a math operation, no dedicated FFT subtree on fw 06.10.
    s.write(':FUNCtion:OPERation FFT')
    s.write(f':FUNCtion:SOURce {chan}')
    s.write(':FUNCtion:DISPlay 1')
    src = s.query(':FUNCtion:SOURce?')
    rng = s.query(':FUNCtion:RANGe?')
    off = s.query(':FUNCtion:OFFSet?')
    print(f'FFT active on {src}: vertical range={rng}, offset={off}')
    if args.window or args.span or args.center:
        print('note: keysight firmware 06.10 does not expose FFT window/span/center via SCPI')
        print('      set via front panel: [Math] → FFT → softkeys')
    errs = [e for e in s.drain_errors() if not _is_no_error(e)]
    for e in errs: print('  WARN', e)


# ---------- labels / display ----------

def cmd_label(s: Scope, args):
    chan = parse_chan(args.channel)
    if s.vendor.name == 'rigol':
        # DS1000Z: CHANnel<n>:LABel exists but needs LABel:ENABle to render.
        try:
            s.write(f':{chan}:LABel:CONTent "{args.text}"')
            s.write(f':{chan}:LABel:ENABle ON')
            s.write(':DISPlay:LABel ON')
        except Exception:
            s.drain_errors()
            print('label may not be supported on this firmware', file=sys.stderr)
            return
    else:
        s.write(f':{chan}:LABel "{args.text}"')
        s.write(':DISPlay:LABel 1')
    print(f'{chan} label = {args.text!r}')


# ---------- setup / waveform memory (Keysight only for now) ----------

def cmd_save_setup(s: Scope, args):
    if s.vendor.name == 'rigol':
        print('save-setup not implemented for RIGOL (uses :STORage:SETup); use front panel.',
              file=sys.stderr)
        sys.exit(2)
    if args.slot.isdigit():
        s.write(f':SAVe:SETup:STARt {args.slot}')
    else:
        s.write(f':SAVe:FILename "{args.slot}"')
        s.write(':SAVe:SETup:STARt')
    print(f'saved setup → {args.slot}')


def cmd_load_setup(s: Scope, args):
    if s.vendor.name == 'rigol':
        print('load-setup not implemented for RIGOL (uses :STORage:SETup); use front panel.',
              file=sys.stderr)
        sys.exit(2)
    if args.slot.isdigit():
        s.write(f':RECall:SETup:LOAD {args.slot}')
    else:
        s.write(f':RECall:FILename "{args.slot}"')
        s.write(':RECall:SETup:STARt')
    print(f'loaded setup ← {args.slot}')


def cmd_save_wmem(s: Scope, args):
    if not s.vendor.has_wmem:
        print(f'save-wmem not implemented for {s.vendor.name} (RIGOL uses :REFerence<n>:).',
              file=sys.stderr)
        sys.exit(2)
    chan = parse_chan(args.channel)
    s.write(f':WMEMory{args.slot}:SAVE {chan}')
    print(f'{chan} → WMEM{args.slot}')


def cmd_show_wmem(s: Scope, args):
    if not s.vendor.has_wmem:
        print(f'show-wmem not implemented for {s.vendor.name} (RIGOL uses :REFerence<n>:).',
              file=sys.stderr)
        sys.exit(2)
    on = args.state.lower() in ('on', '1', 'true')
    s.write(f':WMEMory{args.slot}:DISPlay {1 if on else 0}')
    print(f'WMEM{args.slot} display {"on" if on else "off"}')


# ---------- event-driven monitor ----------

def cmd_monitor(s: Scope, args):
    out_dir = pathlib.Path(args.out_dir or f'monitor_{dt.datetime.now():%Y%m%d_%H%M%S}')
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    ext = s.vendor.screenshot_ext
    print(f'monitoring → {out_dir} (Ctrl-C to stop)')
    try:
        while args.count == 0 or n < args.count:
            s.set_timeout(DEEP_TIMEOUT_MS)
            s.write(':STOP')
            s.write(':SINGle')
            if s.vendor.name == 'rigol':
                # Poll :TRIG:STATus until not WAIT.
                while s.query(':TRIGger:STATus?') == 'WAIT':
                    time.sleep(0.1)
            else:
                while True:
                    cond = int(s.query(':OPERegister:CONDition?') or '0')
                    if not (cond & (1 << 3)):
                        break
                    time.sleep(0.1)
            ts = dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            shot = out_dir / f'{ts}.{ext}'
            s.set_timeout(SCREENSHOT_TIMEOUT_MS)
            shot.write_bytes(s.queryb(s.vendor.screenshot_query))
            for ch in s.vendor.channels:
                try:
                    on = s.query(f':CHANnel{ch}:DISPlay?') in ('1', 'ON')
                except Exception:
                    s.drain_errors()
                    continue
                if on:
                    csv = out_dir / f'{ts}_CHAN{ch}.csv'
                    _dump_waveform(s, f'CHAN{ch}', csv, mode='RAW')
            n += 1
            print(f'[{n}] captured {ts}')
    except KeyboardInterrupt:
        print(f'\nstopped after {n} captures')


# ---------- raw escape hatch ----------

def cmd_raw(s: Scope, args):
    cmd = args.scpi
    if cmd.strip().endswith('?'):
        print(s.query(cmd))
    else:
        s.write(cmd)
        for e in s.drain_errors():
            if not _is_no_error(e):
                print('ERR:', e, file=sys.stderr)


def _usbtmc_clear(dev, interface_num=0, verbose=True):
    """USBTMC INITIATE_CLEAR + CHECK_CLEAR_STATUS + clear_halt dance.

    Drains a stalled pipe without a physical replug. Works for any USBTMC
    device (both Keysight and RIGOL). Spec: USBTMC 1.0 §4.2.1.6-4.2.1.7,
    bmRequestType = 0xA1.
    """
    INITIATE_CLEAR = 5
    CHECK_CLEAR_STATUS = 6
    STATUS_SUCCESS, STATUS_PENDING = 0x01, 0x02
    bmRequestType = 0xA1
    log = print if verbose else (lambda *a, **k: None)

    try:
        if dev.is_kernel_driver_active(interface_num):
            dev.detach_kernel_driver(interface_num)
    except Exception:
        pass
    try:
        usb.util.claim_interface(dev, interface_num)
    except usb.core.USBError as e:
        log(f'  claim interface: {e}')
        return False

    try:
        r = dev.ctrl_transfer(bmRequestType, INITIATE_CLEAR, 0, interface_num, 1, timeout=1000)
        log(f'  INITIATE_CLEAR status={r[0]:#04x}')
    except usb.core.USBError as e:
        log(f'  INITIATE_CLEAR: {e}')
        try: usb.util.release_interface(dev, interface_num)
        except Exception: pass
        return False

    for _ in range(20):
        try:
            r = dev.ctrl_transfer(bmRequestType, CHECK_CLEAR_STATUS, 0, interface_num, 2, timeout=1000)
            log(f'  CHECK_CLEAR_STATUS status={r[0]:#04x}')
            if r[0] == STATUS_SUCCESS:
                break
            if r[0] != STATUS_PENDING:
                log('  unexpected status — giving up')
                break
            time.sleep(0.05)
        except usb.core.USBError as e:
            log(f'  CHECK_CLEAR_STATUS: {e}')
            break

    # Discover bulk endpoints from the descriptor — Keysight uses 0x02/0x86,
    # RIGOL DS1000Z uses 0x03/0x82, so hardcoding either set breaks the other.
    bulk_in = bulk_out = None
    for cfg in dev:
        for intf in cfg:
            if intf.bInterfaceNumber != interface_num:
                continue
            for ep in intf:
                if usb.util.endpoint_type(ep.bmAttributes) != usb.util.ENDPOINT_TYPE_BULK:
                    continue
                if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                    bulk_in = ep.bEndpointAddress
                else:
                    bulk_out = ep.bEndpointAddress
    for ep_addr in filter(None, (bulk_in, bulk_out)):
        try:
            dev.clear_halt(ep_addr)
            log(f'  clear_halt {ep_addr:#04x} ok')
        except usb.core.USBError as e:
            log(f'  clear_halt {ep_addr:#04x}: {e}')
    # INITIATE_CLEAR per USBTMC §4.2.1.6 aborts in-flight transfers but leaves any
    # already-queued response data in the bulk-IN buffer. pyvisa-py then parses
    # that stale data as the next response and chokes ("buffer of 2 bytes").
    # Drain bulk-IN explicitly here while we still hold the interface.
    if bulk_in is not None:
        for _ in range(20):
            try:
                chunk = dev.read(bulk_in, 4096, timeout=100)
                log(f'  drained {len(chunk)} stale bulk-IN bytes')
                if not chunk:
                    break
            except usb.core.USBError:
                break
    try:
        usb.util.release_interface(dev, interface_num)
    except Exception:
        pass
    return True


def cmd_recover(_unused, args):
    """Recover from USBTMC pipe stall.

    Two-stage:
      1. USBTMC INITIATE_CLEAR + clear_halt on bulk endpoints. Clears most stalls.
      2. If that's not enough (RIGOL DS1000Z can leave its USBTMC state machine
         desynced so the next bulk-IN read returns a 2-byte partial header), fall
         back to ``dev.reset()`` — a USB port reset that emulates a replug without
         physically touching the cable.
    """
    del _unused
    addr = args.addr or discover_addr()
    m = re.search(r'USB0?::(\d+)::(\d+)::', addr)
    if not m:
        print('cannot parse VID/PID from', addr); sys.exit(1)
    vid, pid = int(m.group(1)), int(m.group(2))
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if not dev:
        print(f'device {vid:#06x}:{pid:#06x} not on USB bus — replug the scope')
        sys.exit(1)
    print(f'found {vid:#06x}:{pid:#06x} at bus {dev.bus} addr {dev.address}')
    _usbtmc_clear(dev)
    usb.util.dispose_resources(dev)
    time.sleep(0.5)

    def _try_reconnect():
        rm = pyvisa.ResourceManager('@py')
        d = rm.open_resource(addr, timeout=3000)
        try: return d.query('*IDN?').strip()
        finally: d.close()

    try:
        idn = _try_reconnect()
        print('reconnect:', idn); print('OK'); return
    except Exception as e:
        print('reconnect after INITIATE_CLEAR failed:', e)

    print('escalating: usb port reset (soft replug)')
    dev2 = usb.core.find(idVendor=vid, idProduct=pid)
    if not dev2:
        print(f'device {vid:#06x}:{pid:#06x} not on USB bus — replug the scope')
        sys.exit(2)
    try:
        dev2.reset()
        print('  dev.reset() ok')
    except Exception as e:
        print('  dev.reset() failed:', e)
    usb.util.dispose_resources(dev2)
    time.sleep(1.5)
    try:
        idn = _try_reconnect()
        print('reconnect:', idn); print('OK')
    except Exception as e:
        print('reconnect still failing:', e)
        print('--> power-cycle the scope from the front panel')
        sys.exit(2)


# ---------- CLI wiring ----------

def build_parser():
    p = argparse.ArgumentParser(prog='scope.py',
                                description='Multi-vendor scope control (Keysight DSO5000 / RIGOL DS1000Z)')
    p.add_argument('--addr', default=None,
                   help='VISA resource string (default: auto-discover)')
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('idn',      help='print *IDN?').set_defaults(fn=cmd_idn)
    sub.add_parser('state',    help='dump timebase/channels/trigger').set_defaults(fn=cmd_state)
    sub.add_parser('err',      help='drain SCPI error queue').set_defaults(fn=cmd_err)
    sub.add_parser('selftest', help='run *TST?').set_defaults(fn=cmd_selftest)
    sub.add_parser('beep',     help='remote beep').set_defaults(fn=cmd_beep)
    sub.add_parser('reset',    help='*RST to defaults').set_defaults(fn=cmd_reset)
    sub.add_parser('run',      help='acquisition run').set_defaults(fn=cmd_run)
    sub.add_parser('stop',     help='acquisition stop').set_defaults(fn=cmd_stop)
    sub.add_parser('single',   help='acquisition single').set_defaults(fn=cmd_single)
    sub.add_parser('auto',     help='autoscale').set_defaults(fn=cmd_auto)

    sp = sub.add_parser('screenshot', help='save display (vendor-native format: BMP/PNG)')
    sp.add_argument('-o', '--out', help='output path (extension auto-set if omitted)')
    sp.set_defaults(fn=cmd_screenshot)

    sp = sub.add_parser('meas', help='auto-measurements on channel')
    sp.add_argument('channel')
    sp.add_argument('--list', action='store_true', help='list available measurements')
    sp.add_argument('--one', help='one measurement, e.g. FREQ VPP RISE')
    sp.set_defaults(fn=cmd_meas)

    sp = sub.add_parser('csv', help='screen-depth waveform CSV')
    sp.add_argument('channel')
    sp.add_argument('-o', '--out')
    sp.set_defaults(fn=cmd_csv)

    sp = sub.add_parser('deep', help='deep-memory waveform CSV')
    sp.add_argument('channel')
    sp.add_argument('-n', '--points', type=int, default=1_000_000)
    sp.add_argument('-o', '--out')
    sp.set_defaults(fn=cmd_deep)

    sp = sub.add_parser('trig', help='trigger configuration')
    tsub = sp.add_subparsers(dest='trigmode', required=True)
    t1 = tsub.add_parser('state'); t1.set_defaults(fn=cmd_trig_state)
    t2 = tsub.add_parser('edge')
    t2.add_argument('channel'); t2.add_argument('level', type=float)
    t2.add_argument('slope', nargs='?', default='POS', choices=['POS','NEG','EITH','pos','neg','eith'])
    t2.add_argument('--sweep', choices=['AUTO','NORM','auto','norm'])
    t2.set_defaults(fn=cmd_trig_edge)
    t3 = tsub.add_parser('glitch', help='pulse-width trigger (Keysight: GLITch / RIGOL: PULSe)')
    t3.add_argument('channel'); t3.add_argument('polarity', choices=['pos','neg','POS','NEG'])
    t3.add_argument('qual', choices=['GREaterthan','LESSthan','RANGe','GRE','LESS','RANG'])
    t3.add_argument('t1', type=float); t3.add_argument('t2', type=float, nargs='?')
    t3.set_defaults(fn=cmd_trig_glitch)

    sp = sub.add_parser('mask', help='mask test (Keysight only — AMASK auto-mask)')
    msub = sp.add_subparsers(dest='maskop', required=True)
    m1 = msub.add_parser('create')
    m1.add_argument('channel', nargs='?', default='CHAN1')
    m1.add_argument('-x', '--xdel', type=float, default=0.05)
    m1.add_argument('-y', '--ydel', type=float, default=0.10)
    m1.set_defaults(fn=cmd_mask_create)
    ml = msub.add_parser('load', help='load .msk file from USB drive path on scope')
    ml.add_argument('path'); ml.set_defaults(fn=cmd_mask_load)
    m2 = msub.add_parser('run')
    m2.add_argument('-n', '--count', type=int, default=0, help='waveforms; 0 = forever (Ctrl-C)')
    m2.set_defaults(fn=cmd_mask_run)
    msub.add_parser('stats').set_defaults(fn=cmd_mask_stats)
    msub.add_parser('off').set_defaults(fn=cmd_mask_off)

    sp = sub.add_parser('math', help='math channel (ADD SUBT MULT INTegrate DIFF FFT)')
    sp.add_argument('op'); sp.add_argument('src1'); sp.add_argument('src2', nargs='?')
    sp.set_defaults(fn=cmd_math)

    sp = sub.add_parser('fft', help='FFT on channel')
    sp.add_argument('channel')
    sp.add_argument('-w', '--window', choices=['HANN','RECT','FLAT','BHAR','hann','rect','flat','bhar'])
    sp.add_argument('--span', type=float); sp.add_argument('--center', type=float)
    sp.set_defaults(fn=cmd_fft)

    sp = sub.add_parser('label', help='set channel label overlay')
    sp.add_argument('channel'); sp.add_argument('text')
    sp.set_defaults(fn=cmd_label)

    sp = sub.add_parser('save-setup', help='save scope setup (Keysight only)')
    sp.add_argument('slot'); sp.set_defaults(fn=cmd_save_setup)
    sp = sub.add_parser('load-setup', help='recall scope setup (Keysight only)')
    sp.add_argument('slot'); sp.set_defaults(fn=cmd_load_setup)

    sp = sub.add_parser('save-wmem', help='save live waveform to WMEM<n> (Keysight only)')
    sp.add_argument('slot', type=int, choices=[1,2,3,4]); sp.add_argument('channel')
    sp.set_defaults(fn=cmd_save_wmem)
    sp = sub.add_parser('show-wmem', help='toggle WMEM<n> display (Keysight only)')
    sp.add_argument('slot', type=int, choices=[1,2,3,4]); sp.add_argument('state', choices=['on','off','1','0'])
    sp.set_defaults(fn=cmd_show_wmem)

    sp = sub.add_parser('monitor', help='trigger-driven capture loop: BMP/PNG + CSV per event')
    sp.add_argument('-o', '--out-dir'); sp.add_argument('-n', '--count', type=int, default=0)
    sp.set_defaults(fn=cmd_monitor)

    sp = sub.add_parser('raw', help='send arbitrary SCPI (auto-detects query by trailing ?)')
    sp.add_argument('scpi'); sp.set_defaults(fn=cmd_raw)

    sp = sub.add_parser('recover',
        help='recover from USB pipe stall (does NOT open VISA session)')
    sp.set_defaults(fn=cmd_recover, skip_open=True)

    return p


def main():
    args = build_parser().parse_args()
    if getattr(args, 'skip_open', False):
        args.fn(None, args)
        return
    try:
        scope = Scope(addr=args.addr)
    except (pyvisa.errors.VisaIOError, usb.core.USBError) as e:
        print('cannot open scope:', e, file=sys.stderr)
        print('try:  scope.py recover', file=sys.stderr)
        sys.exit(3)
    try:
        args.fn(scope, args)
    except pyvisa.errors.VisaIOError as e:
        print('VISA error:', e, file=sys.stderr)
        try:
            for err in scope.drain_errors():
                if not _is_no_error(err):
                    print('SCPI:', err, file=sys.stderr)
        except Exception:
            pass
        sys.exit(2)
    except (usb.core.USBError, ValueError) as e:
        # pyvisa-py wraps USBError as ValueError on write; detect by message
        msg = str(e)
        if 'Pipe error' in msg or 'Input/Output Error' in msg or isinstance(e, usb.core.USBError):
            print('USB pipe stall:', msg, file=sys.stderr)
            print('attempting automatic recover...', file=sys.stderr)
            try: scope.close()
            except Exception: pass
            # Pass the resolved addr to recover (args.addr may have been None).
            args.addr = scope.addr
            cmd_recover(None, args)
            sys.exit(2)
        raise
    finally:
        try: scope.close()
        except Exception: pass


if __name__ == '__main__':
    main()
