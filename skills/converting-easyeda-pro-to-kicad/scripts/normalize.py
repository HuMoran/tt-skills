import json, os, sys, glob
import pcbnew
from pcbnew import UTILS_STEP_MODEL

RAW = '/tmp/e3/raw'
OUT = '/Users/tao/Work/Hertzbio/nfs/nfs-hw/mcb/EASYEDA_MODELS'
os.makedirs(OUT, exist_ok=True)

man = json.load(open('/tmp/e3/manifest.json'))
report = []
for path in sorted(glob.glob(os.path.join(RAW, '*.step'))):
    title = os.path.splitext(os.path.basename(path))[0]
    model = UTILS_STEP_MODEL.LoadSTEP(path)
    if not model:
        report.append((title, 'LOAD-FAIL', '', ''))
        continue
    bbox = model.GetBoundingBox()
    size = bbox.GetSize()
    note = ''
    tr = man.get(title, {}).get('transform', '')
    if tr:
        arr = [float(x) for x in tr.split(',')]
        fitX, fitY = arr[0] / 39.37, arr[1] / 39.37
        if fitX > 0 and fitY > 0 and size.x > 0 and size.y > 0:
            sx, sy = fitX / size.x, fitY / size.y
            s = (sx + sy) / 2
            if abs(sx - sy) > 0.1:
                note = f'MISORIENT? sx={sx:.3f} sy={sy:.3f}'
            elif abs(s - 1.0) > 0.01:
                model.Scale(s)
                note = f'scaled x{s:.4f}'
    nb = model.GetBoundingBox()
    c = nb.GetCenter()
    model.Translate(-c.x, -c.y, -nb.Min().z)
    out = os.path.join(OUT, title + '.step')
    model.SaveSTEP(out)
    fs = nb.GetSize()
    report.append((title, 'ok', f'{fs.x:.2f}x{fs.y:.2f}x{fs.z:.2f}mm', note))

w = max(len(r[0]) for r in report)
for r in report:
    print(r[0].ljust(w), r[1], r[2], r[3])
bad = [r for r in report if r[1] != 'ok' or 'MISORIENT' in r[3]]
print(f'\nnormalized {sum(1 for r in report if r[1]=="ok")}/{len(report)}, flags: {len(bad)}')
