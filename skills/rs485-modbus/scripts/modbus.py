#!/usr/bin/env python3
"""Tiny Modbus RTU master over a USB-to-RS485 dongle.

Generic core + emst-core preset. Pyserial + hand-rolled CRC. No retries.
"""
from __future__ import annotations

import argparse
import glob
import struct
import sys
from dataclasses import dataclass

try:
    import serial  # pyserial
except ImportError:
    sys.exit(
        "missing pyserial — run: bash $HOME/.claude/skills/rs485-modbus/scripts/setup.sh"
    )


PRESETS: dict[str, dict] = {
    "emst-core": {
        "port": "/dev/cu.usbserial-A50285BI",
        "baud": 115200,
        "slave": 1,
        "parity": "N",
        "stop_bits": 1,
        "data_bits": 8,
    },
}

EXCEPTION_NAMES = {
    1: "ILLEGAL_FUNCTION",
    2: "ILLEGAL_DATA_ADDRESS",
    3: "ILLEGAL_DATA_VALUE",
    4: "SLAVE_DEVICE_FAILURE",
    5: "ACKNOWLEDGE",
    6: "SLAVE_DEVICE_BUSY",
    8: "MEMORY_PARITY_ERROR",
    10: "GATEWAY_PATH_UNAVAILABLE",
    11: "GATEWAY_TARGET_NO_RESPONSE",
}


class ModbusException(Exception):
    def __init__(self, code: int):
        self.code = code
        super().__init__(f"code={code} {EXCEPTION_NAMES.get(code, '?')}")


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, "little")


def parse_int(s: str) -> int:
    s = s.strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def parse_int_list(s: str) -> list[int]:
    return [parse_int(x) for x in s.split(",") if x.strip()]


@dataclass
class Link:
    port: str
    baud: int
    slave: int
    parity: str  # "N" / "E" / "O"
    stop_bits: int
    data_bits: int
    timeout: float

    def open(self) -> serial.Serial:
        parity_map = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}
        return serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=self.data_bits,
            parity=parity_map[self.parity.upper()],
            stopbits=self.stop_bits,
            timeout=self.timeout,
        )


def autodetect_port() -> str | None:
    for pat in ("/dev/cu.usbserial*", "/dev/cu.SLAB*", "/dev/cu.wchusb*"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    return None


def link_from_args(args: argparse.Namespace) -> Link:
    preset = PRESETS.get(args.preset, {}) if args.preset else {}

    def pick(name: str, fallback):
        v = getattr(args, name, None)
        if v is not None:
            return v
        if name in preset:
            return preset[name]
        return fallback

    port = pick("port", None) or autodetect_port()
    if not port:
        sys.exit("no port given and no /dev/cu.usbserial* / cu.SLAB* / cu.wchusb* found")

    return Link(
        port=port,
        baud=pick("baud", 115200),
        slave=pick("slave", 1),
        parity=pick("parity", "N"),
        stop_bits=pick("stop_bits", 1),
        data_bits=pick("data_bits", 8),
        timeout=args.timeout,
    )


def request(ser: serial.Serial, slave: int, fc: int, payload: bytes) -> bytes:
    """Send a Modbus RTU frame, return the data portion of the response.

    Raises TimeoutError on no reply, ValueError on CRC/address mismatch,
    ModbusException on FC|0x80 exception responses.
    """
    frame = bytes([slave, fc]) + payload
    frame += crc16(frame)
    ser.reset_input_buffer()
    ser.write(frame)

    raw = ser.read(256)
    if not raw:
        raise TimeoutError(f"no response (sent: {frame.hex()})")
    if len(raw) < 4:
        raise ValueError(f"short frame: {raw.hex()}")
    if raw[0] != slave:
        raise ValueError(f"slave mismatch: got {raw[0]:#04x}, want {slave:#04x}; raw={raw.hex()}")

    body, recv_crc = raw[:-2], raw[-2:]
    if crc16(body) != recv_crc:
        raise ValueError(f"CRC mismatch: got {recv_crc.hex()}, computed {crc16(body).hex()}; raw={raw.hex()}")

    if raw[1] & 0x80:
        raise ModbusException(raw[2])
    if raw[1] != fc:
        raise ValueError(f"FC mismatch: sent {fc:#04x}, got {raw[1]:#04x}; raw={raw.hex()}")

    return body[2:]  # strip slave + FC


def cmd_ports(_: argparse.Namespace) -> int:
    hits: list[str] = []
    for pat in ("/dev/cu.usbserial*", "/dev/cu.SLAB*", "/dev/cu.wchusb*", "/dev/cu.usbmodem*"):
        hits.extend(glob.glob(pat))
    if not hits:
        print("(no USB serial devices found)")
        print("hint: plug in your USB-RS485 adapter, then re-run.")
        return 1
    for p in sorted(set(hits)):
        print(p)
    return 0


def _read_regs(args: argparse.Namespace, fc: int, addr: int, count: int) -> list[int]:
    link = link_from_args(args)
    with link.open() as ser:
        payload = struct.pack(">HH", addr, count)
        data = request(ser, link.slave, fc, payload)
    bcount = data[0]
    if bcount != 2 * count:
        raise ValueError(f"unexpected byte count: {bcount} (want {2 * count})")
    return list(struct.unpack(f">{count}H", data[1 : 1 + bcount]))


def cmd_ping(args: argparse.Namespace) -> int:
    link = link_from_args(args)
    regs = _read_regs(args, fc=0x04, addr=0x00, count=2)
    if args.preset == "emst-core":
        major = (regs[0] >> 8) & 0xFF
        minor = regs[0] & 0xFF
        patch = (regs[1] >> 8) & 0xFF
        rsv = regs[1] & 0xFF
        print(f"OK  port={link.port}  slave={link.slave}  VERSION={major}.{minor}.{patch}  reserved={rsv}")
    else:
        print(f"OK  port={link.port}  slave={link.slave}  input[0..1]={[hex(r) for r in regs]}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    fc = 0x03 if args.type == "holding" else 0x04
    regs = _read_regs(args, fc=fc, addr=args.addr, count=args.count)
    label = "holding" if args.type == "holding" else "input"
    print(f"{label} regs from {args.addr:#06x} (count={args.count}):")
    for i, r in enumerate(regs):
        print(f"  {args.addr + i:#06x}: {r:#06x}  ({r})")
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    link = link_from_args(args)
    with link.open() as ser:
        if len(args.values) == 1:
            payload = struct.pack(">HH", args.addr, args.values[0])
            request(ser, link.slave, 0x06, payload)
        else:
            n = len(args.values)
            payload = struct.pack(">HHB", args.addr, n, n * 2)
            for v in args.values:
                payload += struct.pack(">H", v)
            request(ser, link.slave, 0x10, payload)
    fc_used = "FC=06" if len(args.values) == 1 else "FC=10"
    pretty = ",".join(f"{v:#06x}" for v in args.values)
    print(f"OK  {fc_used} slave={link.slave} addr={args.addr:#06x} values=[{pretty}]")
    return 0


def add_link_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--preset", choices=list(PRESETS), default=None)
    p.add_argument("--port", default=None, help="serial device path")
    p.add_argument("--baud", type=int, default=None)
    p.add_argument("--slave", type=parse_int, default=None, help="Modbus slave address (1..247)")
    p.add_argument("--parity", choices=["N", "E", "O"], default=None)
    p.add_argument("--stop-bits", dest="stop_bits", type=int, choices=[1, 2], default=None)
    p.add_argument("--data-bits", dest="data_bits", type=int, choices=[7, 8], default=None)
    p.add_argument("--timeout", type=float, default=0.3, help="response timeout in seconds")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="modbus", description="Tiny Modbus RTU master over USB-to-RS485")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ports", help="list USB serial devices")
    sp.set_defaults(func=cmd_ports)

    sp = sub.add_parser("ping", help="read VERSION (FC=04 input regs 0..1)")
    add_link_args(sp)
    sp.set_defaults(func=cmd_ping)

    sp = sub.add_parser("read", help="read holding (FC=03) or input (FC=04) registers")
    add_link_args(sp)
    sp.add_argument("--type", choices=["holding", "input"], required=True)
    sp.add_argument("--addr", type=parse_int, required=True, help="start register address")
    sp.add_argument("--count", type=parse_int, required=True, help="number of 16-bit registers")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("write", help="write holding regs (FC=06 single, FC=10 multi)")
    add_link_args(sp)
    sp.add_argument("--addr", type=parse_int, required=True, help="start register address")
    sp.add_argument("--values", type=parse_int_list, required=True, help="comma-separated 16-bit values")
    sp.set_defaults(func=cmd_write)

    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (TimeoutError, ValueError, ModbusException, serial.SerialException) as e:
        print(f"ERR  {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
