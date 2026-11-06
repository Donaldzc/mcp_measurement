# -*- coding: utf-8 -*-
"""A2/drift proxy: static tool-name-set drift across version pairs.

Design (approved): per literal-pkg server, most recent 10 consecutive version
pairs + seeded uniform sample of up to 30 older pairs. Same static extractor as
the paper (static_tools.js, acorn AST). Output: _drift_scan/summary.json.
"""
import json, pathlib, re, subprocess, sys, tarfile, urllib.request, random, time

BASE = pathlib.Path('d:/code/trae/temp_20261021_02/mcp_measurement')
PACK = json.load(open(BASE / 'sandbox/cache/npm_packument_trimmed.json', encoding='utf-8'))
ACORN = BASE / 'sandbox_cache/deps/mastra_mcp_docs_server/node_modules/acorn'
SCRIPT = BASE / 'static_tools.js'
WORK = BASE / '_drift_scan'
(WORK / 'src').mkdir(parents=True, exist_ok=True)
LOG = open(WORK / 'run.log', 'w', encoding='utf-8')

def log(*a):
    msg = ' '.join(str(x) for x in a)
    print(msg, flush=True)
    LOG.write(msg + '\n'); LOG.flush()

def san(n):
    return re.sub(r'[^a-z0-9]+', '_', n.lower()).strip('_')

def wilson(k, n, z=1.96):
    if n == 0: return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0, (c - m) / d), min(1, (c + m) / d))

F3 = json.load(open(BASE / 'sandbox/output/f3_report.json', encoding='utf-8'))
by_id = {san(k): k for k in PACK}
SERVERS = sorted(s['server_id'] for s in F3['per_server']
                 if s['registration_style'] == 'literal_registration' and s['n_target_calls'] > 0)

def versions_by_time(pkg):
    v = PACK[pkg]['versions']
    t = PACK[pkg]['time']
    return sorted(v.keys(), key=lambda x: t.get(x, ''))

def tarball_url(pkg, ver):
    return f'https://registry.npmjs.org/{pkg}/-/{pkg.split("/")[-1]}-{ver}.tgz'

def fetch(pkg, ver, dest):
    if dest.exists() and dest.stat().st_size > 0:
        return True
    url = tarball_url(pkg, ver)
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'drift-scan/1.0'})
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, 'wb') as f:
                f.write(r.read())
            return True
        except Exception as e:
            log('  dl fail', pkg, ver, type(e).__name__)
            time.sleep(2)
    return False

def extract_names(tgz, outdir):
    if outdir.exists() and any(outdir.iterdir()):
        pass
    else:
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(tgz, 'r:gz') as tf:
                tf.extractall(outdir, filter='data')
        except Exception as e:
            log('  untar fail', tgz.name, type(e).__name__)
            return None
    try:
        r = subprocess.run(['node', str(SCRIPT), str(ACORN), str(outdir)],
                           capture_output=True, text=True, timeout=120)
        j = json.loads(r.stdout)
        return set(j.get('calls', [])) | set(j.get('nameProps', []))
    except Exception as e:
        log('  extract fail', outdir.name, type(e).__name__)
        return None

rng = random.Random(20261104)
overall_pairs = overall_changed = 0
per_server = {}
for sid in SERVERS:
    pkg = by_id.get(sid)
    vs = versions_by_time(pkg)
    n = len(vs)
    if n < 2:
        log(sid, 'SKIP: <2 versions')
        continue
    pair_idx = list(range(1, n))            # pair (vs[i-1], vs[i])
    recent = pair_idx[-10:]
    rest = pair_idx[:-10]
    sample = rng.sample(rest, min(30, len(rest)))
    sel = sorted(set(recent) | set(sample))
    need = sorted({vs[i - 1] for i in sel} | {vs[i] for i in sel})
    names = {}
    ok = True
    for ver in need:
        tgz = WORK / 'src' / f'{sid}__{ver}.tgz'
        if not fetch(pkg, ver, tgz):
            ok = False; break
        ns = extract_names(tgz, WORK / 'src' / f'{sid}__{ver}')
        if ns is None:
            ok = False; break
        names[ver] = ns
    if not ok:
        log(sid, 'SKIP: fetch/extract failure')
        continue
    ch = 0
    det = []
    for i in sel:
        a, b = names[vs[i - 1]], names[vs[i]]
        add, rem = len(b - a), len(a - b)
        if add or rem:
            ch += 1
        det.append({'old': vs[i - 1], 'new': vs[i], 'added': add, 'removed': rem,
                    'n_old': len(a), 'n_new': len(b)})
    per_server[sid] = {'pkg': pkg, 'versions': n, 'pairs': len(sel), 'changed': ch,
                       'adds': sum(d['added'] for d in det), 'dels': sum(d['removed'] for d in det),
                       'detail': det}
    overall_pairs += len(sel); overall_changed += ch
    log(f'{sid}: {ch}/{len(sel)} pairs changed (of {n - 1} total pairs)')

rate = overall_changed / overall_pairs if overall_pairs else 0
lo, hi = wilson(overall_changed, overall_pairs)
summary = {'design': 'recent 10 pairs + seeded(20261104) uniform <=30 older pairs per server',
           'servers': per_server, 'pairs': overall_pairs, 'changed': overall_changed,
           'rate': round(rate, 4), 'wilson95': [round(lo, 4), round(hi, 4)]}
json.dump(summary, open(WORK / 'summary.json', 'w', encoding='utf-8'), indent=1)
log('TOTAL', overall_changed, '/', overall_pairs, '=', round(rate, 4), 'CI', [round(lo, 4), round(hi, 4)])
LOG.close()
