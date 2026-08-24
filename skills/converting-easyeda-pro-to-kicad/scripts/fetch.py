import json, os, re, time, subprocess, urllib.parse

PCB = '/Users/tao/Work/Hertzbio/nfs/nfs-hw/mcb/nfs-mcb.kicad_pcb'
PRJ = '/tmp/epro2_x/epro_x/project.json'
RAW = '/tmp/e3/raw'
os.makedirs(RAW, exist_ok=True)

CURL = ['curl', '-sS', '-m', '60', '--fail',
        '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36',
        '-H', 'Referer: https://pro.easyeda.com/editor',
        '-H', 'X-Requested-With: XMLHttpRequest']

def http(url, data=None, binary=False, tries=3):
    cmd = list(CURL)
    if data is not None:
        cmd += ['--data', urllib.parse.urlencode(data, doseq=True)]
    cmd.append(url)
    for i in range(tries):
        try:
            raw = subprocess.run(cmd, capture_output=True, check=True).stdout
            return raw if binary else json.loads(raw)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2)

# 1. needed model titles from pcb
needed = sorted(set(re.findall(r'\(model "\$\{KIPRJMOD\}/EASYEDA_MODELS/([^"]+)\.step"', open(PCB).read())))
print(f'needed titles: {len(needed)}')

# 2. local device metadata: title -> {codes, transform}
prj = json.load(open(PRJ))
local = {}
for v in prj['devices'].values():
    a = v.get('attributes', {})
    t = a.get('3D Model Title', '')
    if not t or t not in needed:
        continue
    e = local.setdefault(t, {'codes': [], 'transform': a.get('3D Model Transform', '')})
    c = a.get('Supplier Part', '')
    if c and c not in e['codes']:
        e['codes'].append(c)

missing_meta = [t for t in needed if t not in local or not local[t]['codes']]
print('titles without LCSC code:', missing_meta)

# 3. searchByCodes in chunks
all_codes = sorted({c for e in local.values() for c in e['codes']})
cloud_by_code = {}
for i in range(0, len(all_codes), 5):
    chunk = all_codes[i:i+5]
    r = http('https://pro.easyeda.com/api/v2/devices/searchByCodes', data={'codes[]': chunk})
    for dev in r.get('result', []):
        code = dev.get('attributes', {}).get('Supplier Part', dev.get('product_code', ''))
        cloud_by_code[code] = dev
print(f'searchByCodes: {len(cloud_by_code)}/{len(all_codes)} codes resolved')

# 4. per title: pick cloud device with matching 3D Model Title, chain to direct uuid, download
manifest = {}
for t in needed:
    entry = {'title': t, 'status': None, 'codes': local.get(t, {}).get('codes', []),
             'transform': local.get(t, {}).get('transform', '')}
    manifest[t] = entry
    if t in missing_meta:
        entry['status'] = 'no-lcsc-code'
        continue
    cand = [cloud_by_code[c] for c in entry['codes'] if c in cloud_by_code]
    match = [d for d in cand if d['attributes'].get('3D Model Title') == t]
    dev = (match or cand or [None])[0]
    if dev is None:
        entry['status'] = 'no-cloud-device'
        continue
    entry['cloud_code'] = dev['attributes'].get('Supplier Part')
    entry['title_match'] = bool(match)
    entry['cloud_title'] = dev['attributes'].get('3D Model Title')
    shell = (dev['attributes'].get('3D Model') or '').split('|')[0]
    if not shell:
        entry['status'] = 'cloud-device-has-no-model'
        continue
    try:
        comp = http(f'https://pro.easyeda.com/api/v2/components/{shell}')
        ds = json.loads(comp['result']['dataStr'])
        direct = ds['model']
        entry['direct_uuid'] = direct
        step = http(f'https://modules.easyeda.com/qAxj6KHrDKw4blvCG8QJPs7Y/{direct}', binary=True)
        if not step.startswith(b'ISO-10303-21'):
            entry['status'] = 'not-step: ' + step[:40].decode('ascii', 'replace')
            continue
        open(os.path.join(RAW, t + '.step'), 'wb').write(step)
        entry['status'] = 'ok'
        entry['bytes'] = len(step)
    except Exception as e:
        entry['status'] = f'error: {e}'
    print(t, '->', entry['status'], '' if entry.get('title_match', True) else f"(cloud title: {entry['cloud_title']})")

json.dump(manifest, open('/tmp/e3/manifest.json', 'w'), indent=1)
ok = sum(1 for e in manifest.values() if e['status'] == 'ok')
print(f'\nDOWNLOADED {ok}/{len(needed)}')
for t, e in manifest.items():
    if e['status'] != 'ok':
        print('FAIL', t, e['status'])
