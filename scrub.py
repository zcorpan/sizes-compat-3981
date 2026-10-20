"""Scrub committed browser output: the tester's IP, and query strings/fragments of URLs that aren't
crawl URLs. Crawl URLs = `page` values plus URLs found in the crawl input files given as args."""
import csv, glob, json, re, sys
csv.field_size_limit(sys.maxsize)
IP = sys.argv[3]  # the tester's public IP, as shown on block and captcha pages
KEEP = {'page', 'xsltStacks'}  # crawl URL; script URLs in stacks (analysis parses them)
URL = re.compile(r'https?://[^\s"\'<>]+')
inputs, outputs = sys.argv[1].split(','), sys.argv[2].split(',')
allowed = set()
def collect(x):
    if isinstance(x, str):
        allowed.update(URL.findall(x)); allowed.add(x)
    elif isinstance(x, dict):
        for v in x.values(): collect(v)
    elif isinstance(x, list):
        for v in x: collect(v)
for pat in inputs:
    for f in glob.glob(pat):
        if f.endswith('.csv'):
            for row in csv.reader(open(f, newline='', errors='replace')): collect(row)
        elif f.endswith('.jsonl'):
            for l in open(f):
                if l.strip(): collect(json.loads(l))
        else:
            collect(json.load(open(f)))
def strip_url(m):
    u = m.group(0)
    if u in allowed or u.rstrip('.,;)') in allowed:
        return u
    return re.split(r'[?#]', u, maxsplit=1)[0]
def s(x, key=None):
    if isinstance(x, str):
        x = x.replace(IP, '[client IP]')
        return x if key in KEEP else URL.sub(strip_url, x)
    if isinstance(x, dict):
        return {k: s(v, k) for k, v in x.items()}
    if isinstance(x, list):
        return [s(v, key) for v in x]
    return x
for pat in outputs:
    for f in glob.glob(pat):
        recs = [json.loads(l) for l in open(f) if l.strip()]
        for r in recs:
            if 'page' in r: allowed.add(r['page'])
        open(f, 'w').write(''.join(json.dumps(s(r)) + '\n' for r in recs))
