#!/usr/bin/env python3
"""kicad_sch 几何模型: 符号引脚绝对坐标 / wire / label / junction / no_connect.
用途: 找悬空线端点与吸附目标. 不改文件, 只分析."""
import re, sys, json
from collections import defaultdict

SCH = "/Users/tao/Work/Hertzbio/nfs/nfs-hw/mcb/nfs-mcb.kicad_sch"

def parse_sexpr(text):
    tok = re.findall(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()"]+', text)
    pos = 0
    def rd():
        nonlocal pos
        t = tok[pos]; pos += 1
        if t == '(':
            lst = []
            while tok[pos] != ')':
                lst.append(rd())
            pos += 1
            return lst
        if t.startswith('"'):
            return t[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        return t
    return rd()

def kids(node, tag):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == tag]

def kid1(node, tag):
    k = kids(node, tag)
    return k[0] if k else None

def build():
    doc = parse_sexpr(open(SCH).read())

    # lib_symbols: lib_id -> unit -> [(pin_number, x, y)]
    libpins = defaultdict(lambda: defaultdict(list))
    ls = kid1(doc, 'lib_symbols')
    for sym in kids(ls, 'symbol'):
        lib_id = sym[1]
        for sub in kids(sym, 'symbol'):
            m = re.match(r'.*_(\d+)_(\d+)$', sub[1])
            unit = int(m.group(1)) if m else 0
            for pin in kids(sub, 'pin'):
                at = kid1(pin, 'at')
                num = kid1(pin, 'number')
                libpins[lib_id][unit].append((num[1], float(at[1]), float(at[2])))

    # symbol instances
    pins = []   # (ref, pad, x, y)
    syms = []
    for s in kids(doc, 'symbol'):
        lib_id = kid1(s, 'lib_id')[1]
        at = kid1(s, 'at')
        x0, y0 = float(at[1]), float(at[2])
        rot = float(at[3]) if len(at) > 3 else 0
        mir = kid1(s, 'mirror')
        mir = mir[1] if mir else None
        unit = kid1(s, 'unit')
        unit = int(unit[1]) if unit else 1
        ref = '?'
        for p in kids(s, 'property'):
            if p[1] == 'Reference':
                ref = p[2]
        syms.append((ref, lib_id, x0, y0, rot, mir, unit))
        pl = libpins[lib_id].get(unit, []) + libpins[lib_id].get(0, [])
        for num, px, py in pl:
            # KiCad 变换: 先 mirror 后 rotate; sch y 轴向下, lib y 轴向上
            lx, ly = px, py
            if mir == 'x': ly = -ly
            elif mir == 'y': lx = -lx
            r = rot % 360
            if r == 90: lx, ly = -ly, lx
            elif r == 180: lx, ly = -lx, -ly
            elif r == 270: lx, ly = ly, -lx
            pins.append((ref, num, round(x0 + lx, 6), round(y0 - ly, 6)))

    wires = []  # (idx, x1,y1,x2,y2)
    for i, w in enumerate(kids(doc, 'wire')):
        pts = kid1(w, 'pts')
        xy = kids(pts, 'xy')
        wires.append((i, float(xy[0][1]), float(xy[0][2]), float(xy[1][1]), float(xy[1][2])))

    labels = []
    for tag in ('label', 'global_label'):
        for l in kids(doc, tag):
            at = kid1(l, 'at')
            labels.append((tag, l[1], float(at[1]), float(at[2])))
    juncs = [(float(kid1(j, 'at') and j[1][1]), float(j[1][2])) for j in kids(doc, 'junction')]
    ncs = [(float(j[1][1]), float(j[1][2])) for j in kids(doc, 'no_connect')]
    return dict(pins=pins, wires=wires, labels=labels, juncs=juncs, ncs=ncs, syms=syms)

def near(x, y, x2, y2, tol=0.001):
    return abs(x - x2) <= tol and abs(y - y2) <= tol

def on_seg(px, py, x1, y1, x2, y2, tol=0.0005):
    if abs((x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)) > tol * max(abs(x2-x1)+abs(y2-y1), 1):
        return False
    return min(x1, x2) - tol <= px <= max(x1, x2) + tol and min(y1, y2) - tol <= py <= max(y1, y2) + tol

if __name__ == '__main__':
    m = build()
    print(f"pins={len(m['pins'])} wires={len(m['wires'])} labels={len(m['labels'])} juncs={len(m['juncs'])} ncs={len(m['ncs'])}")
    # 悬空线端点: 端点不与任何 pin/其他wire(端点或线上)/label/junction 重合
    pts_pins = [(x, y, ('pin', r, p)) for r, p, x, y in m['pins']]
    for i, x1, y1, x2, y2 in m['wires']:
        for (ex, ey) in ((x1, y1), (x2, y2)):
            attached = False
            for (px, py, tag) in pts_pins:
                if near(ex, ey, px, py): attached = True; break
            if not attached:
                for j, a1, b1, a2, b2 in m['wires']:
                    if j == i: continue
                    if on_seg(ex, ey, a1, b1, a2, b2): attached = True; break
            if not attached:
                for tag, name, lx, ly in m['labels']:
                    if near(ex, ey, lx, ly): attached = True; break
            if not attached:
                # 距离最近的 pin (<0.2mm)
                cands = sorted(((abs(ex-px)+abs(ey-py), r, p, px, py) for px, py, (t, r, p) in [(a, b, c) for a, b, c in pts_pins]), key=lambda z: z[0])[:2]
                print(f"DANGLE wire#{i} end ({ex},{ey})  nearest pins: {cands}")
