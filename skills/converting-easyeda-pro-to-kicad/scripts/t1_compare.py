#!/usr/bin/env python3
"""T1: sch netlist vs pcb pad-net 分区等价比对 (键: (ref,pad), 忽略网名)."""
import re, sys, json
from collections import defaultdict

SCH_NET = "/tmp/e1/sch.net"
PCB = "/Users/tao/Work/Hertzbio/nfs/nfs-hw/mcb/nfs-mcb.kicad_pcb"

# ---------- 极简 s-expr tokenizer/parser ----------
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

# ---------- sch netlist ----------
sch = parse_sexpr(open(SCH_NET).read())
sch_nets = {}   # netname -> set of (ref,pad)
node2net_sch = {}
for item in sch:
    if isinstance(item, list) and item and item[0] == 'nets':
        for net in item[1:]:
            if net[0] != 'net': continue
            name = None; nodes = []
            for f in net[1:]:
                if f[0] == 'name': name = f[1]
                elif f[0] == 'node':
                    d = {g[0]: g[1] for g in f[1:] if isinstance(g, list)}
                    nodes.append((d['ref'], d['pin']))
            sch_nets[name] = set(nodes)
            for n in nodes:
                node2net_sch[n] = name

# ---------- pcb: 逐 footprint 提取 ref + pads ----------
# 不全量解析 2.2MB, 用文本扫描: footprint 块从 '\t(footprint' 起, 到下一个同缩进
text = open(PCB).read()
# 找每个 footprint 块
fp_starts = [m.start() for m in re.finditer(r'^\t\(footprint ', text, re.M)]
fp_blocks = []
for i, s in enumerate(fp_starts):
    e = fp_starts[i+1] if i+1 < len(fp_starts) else len(text)
    # 收尾: 找块结束 —— 下一个顶层元素. 用括号配平在 [s,e) 内
    fp_blocks.append(text[s:e])

pcb_pads = {}   # (ref,pad) -> netname   (第一次出现)
pcb_dup = []    # 重复 (ref,pad)
pcb_fps = []    # (ref, fpname, uuid, pos)
for blk in fp_blocks:
    m = re.match(r'\t\(footprint\s+"([^"]*)"', blk)
    fpname = m.group(1) if m else '?'
    mu = re.search(r'\(uuid "([^"]+)"\)', blk)
    uuid = mu.group(1) if mu else '?'
    mp = re.search(r'^\t\t\(at ([-\d.]+) ([-\d.]+)', blk, re.M)
    pos = (mp.group(1), mp.group(2)) if mp else None
    mr = re.search(r'\(property "Reference"\s+"((?:[^"\\]|\\.)*)"', blk)
    ref = mr.group(1) if mr else '?'
    pcb_fps.append((ref, fpname, uuid, pos))
    # pads: (pad "NAME" ... (net N "netname"))
    for pm in re.finditer(r'\(pad\s+"((?:[^"\\]|\\.)*)"', blk):
        # 找该 pad 块内的 net: 从 pad 开始括号配平
        ps = pm.start()
        depth = 0; j = ps
        while j < len(blk):
            if blk[j] == '(': depth += 1
            elif blk[j] == ')':
                depth -= 1
                if depth == 0: break
            j += 1
        pblk = blk[ps:j+1]
        padname = pm.group(1)
        nm = re.search(r'\(net\s+(?:\d+\s+)?"((?:[^"\\]|\\.)*)"\)', pblk)
        net = nm.group(1) if nm else None
        key = (ref, padname)
        if key in pcb_pads:
            pcb_dup.append((key, pcb_pads[key], net, uuid, pos))
        else:
            pcb_pads[key] = net

pcb_nets = defaultdict(set)
for k, v in pcb_pads.items():
    if v is not None:
        pcb_nets[v].add(k)

# ---------- 分区等价比对 ----------
# 只对两边共有的 (ref,pad) 键比分区; 单边键单独列出
sch_keys = set(node2net_sch)
pcb_keys = {k for k, v in pcb_pads.items() if v is not None}
pcb_nonet_keys = {k for k, v in pcb_pads.items() if v is None}
common = sch_keys & pcb_keys
only_sch = sch_keys - set(pcb_pads)
only_pcb = set(pcb_pads) - sch_keys

# 分区: 限制在 common 上, 每个网络的成员集合
def partition(node2net, keys):
    p = defaultdict(set)
    for k in keys:
        p[node2net[k]].add(frozenset([k]) and k)
    return {name: frozenset(mem) for name, mem in p.items()}

node2net_pcb = pcb_pads
ps = defaultdict(set); pp = defaultdict(set)
for k in common:
    ps[node2net_sch[k]].add(k)
    pp[node2net_pcb[k]].add(k)

# 同构判定: 集合的集合相等
ps_sets = {frozenset(v): k for k, v in ps.items()}
pp_sets = {frozenset(v): k for k, v in pp.items()}
matched = set(ps_sets) & set(pp_sets)
sch_unmatched = {ps_sets[s]: s for s in set(ps_sets) - matched}
pcb_unmatched = {pp_sets[s]: s for s in set(pp_sets) - matched}

print(f"sch nets={len(sch_nets)} nodes={len(sch_keys)}")
print(f"pcb pads(with net)={len(pcb_keys)} pads(no net)={len(pcb_nonet_keys)} nets={len(pcb_nets)} dup_pads={len(pcb_dup)}")
print(f"common keys={len(common)}  only_sch={len(only_sch)}  only_pcb={len(only_pcb)}")
print(f"partition blocks: sch={len(ps)} pcb={len(pp)} matched={len(matched)}")
print()
print(f"=== sch-only keys ({len(only_sch)}) ===")
for k in sorted(only_sch): print(" ", k, "->", node2net_sch[k])
print()
print(f"=== pcb-only keys ({len(only_pcb)}) ===")
for k in sorted(only_pcb): print(" ", k, "->", pcb_pads[k])
print()
print(f"=== pcb pads with no net ({len(pcb_nonet_keys)}) ===")
for k in sorted(pcb_nonet_keys): print(" ", k)
print()
print(f"=== duplicate (ref,pad) in pcb ({len(pcb_dup)}) ===")
for d in pcb_dup: print(" ", d)
print()
print(f"=== sch partition blocks unmatched ({len(sch_unmatched)}) ===")
for name, mem in sorted(sch_unmatched.items()):
    print(f"  [{name}] {sorted(mem)}")
print()
print(f"=== pcb partition blocks unmatched ({len(pcb_unmatched)}) ===")
for name, mem in sorted(pcb_unmatched.items()):
    print(f"  [{name}] {sorted(mem)}")
print()
# footprint 清单摘要
refs = [f[0] for f in pcb_fps]
from collections import Counter
c = Counter(refs)
print(f"=== pcb footprints: {len(pcb_fps)}, refs重复: {[ (r,n) for r,n in c.items() if n>1 ]} ===")

json.dump({"sch_nets": {k: sorted(map(list, v)) for k, v in sch_nets.items()},
           "pcb_pads": {f"{r}|{p}": n for (r, p), n in pcb_pads.items()},
           "pcb_fps": pcb_fps},
          open("/tmp/e1/t1_model.json", "w"), indent=1, ensure_ascii=False)
