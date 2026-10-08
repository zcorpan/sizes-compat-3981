"""Emulate the proposed `sizes` rule (whatwg/html#3981) on live pages.

For each loaded <img> whose selected srcset candidate has a `w` descriptor that doesn't match the
resource's real width, rewrite the winning srcset (on the <img> or the matching <source>) to the
single candidate `currentSrc <realWidth>w`. The URL and `sizes` stay the same, so the natural width
becomes the source size, which is what the proposed rule gives for a loaded image.

Measures layout before (two baselines, A and A2, to estimate noise) and after (B).

Usage: python emulate.py sample.json out/ [--limit N] [--concurrency N]
"""

import argparse
import asyncio
import json
import pathlib

from playwright.async_api import async_playwright

# Same viewport, DPR, and UA family as the HTTP Archive mobile crawl (emulated Moto G4).
MOBILE = dict(
    viewport={"width": 360, "height": 640},
    device_scale_factor=3,
    is_mobile=True,
    has_touch=True,
    user_agent=(
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Mobile Safari/537.36"
    ),
)

MEASURE_JS = """
() => {
  const imgs = [...document.images].map((img, i) => {
    const r = img.getBoundingClientRect();
    return {
      i,
      src: img.currentSrc,
      x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: r.height,
      nw: img.naturalWidth, nh: img.naturalHeight,
    };
  });
  const se = document.scrollingElement || document.documentElement;
  return {scrollHeight: se.scrollHeight, scrollWidth: se.scrollWidth, imgs};
}
"""

EMULATE_JS = r"""
async () => {
  // Parse a srcset attribute (https://html.spec.whatwg.org/#parse-a-srcset-attribute), simplified:
  // returns [{url, w, x}] with url resolved against the document base URL.
  function parseSrcset(input) {
    const out = [];
    let pos = 0;
    const ws = /[\t\n\f\r ]/;
    while (pos < input.length) {
      while (pos < input.length && (ws.test(input[pos]) || input[pos] === ',')) pos++;
      if (pos >= input.length) break;
      let start = pos;
      while (pos < input.length && !ws.test(input[pos])) pos++;
      let url = input.slice(start, pos);
      let descriptors = '';
      if (url.endsWith(',')) {
        url = url.replace(/,+$/, '');
      } else {
        let inParens = false;
        start = pos;
        while (pos < input.length) {
          const c = input[pos];
          if (c === '(') inParens = true;
          else if (c === ')') inParens = false;
          else if (c === ',' && !inParens) break;
          pos++;
        }
        descriptors = input.slice(start, pos).trim();
        pos++;
      }
      const cand = {url: new URL(url, document.baseURI).href};
      for (const d of descriptors.split(/\s+/).filter(Boolean)) {
        const m = /^(\d+)w$/.exec(d);
        if (m) cand.w = parseInt(m[1]);
        const x = /^([\d.]+)x$/.exec(d);
        if (x) cand.x = parseFloat(x[1]);
      }
      out.push(cand);
    }
    return out;
  }

  function realWidth(url) {
    return new Promise(resolve => {
      const probe = new Image();
      probe.onload = () => resolve(probe.naturalWidth);
      probe.onerror = () => resolve(null);
      probe.src = url;
    });
  }

  const report = [];
  const pending = [];
  for (const [i, img] of [...document.images].entries()) {
    if (!img.complete || !img.naturalWidth || !img.currentSrc) continue;
    // The element whose srcset contains currentSrc is the one that won selection.
    const candidatesElements = [];
    if (img.parentElement && img.parentElement.localName === 'picture') {
      for (const s of img.parentElement.children) {
        if (s === img) break;
        if (s.localName === 'source' && s.hasAttribute('srcset')) candidatesElements.push(s);
      }
    }
    if (img.hasAttribute('srcset')) candidatesElements.push(img);
    let winner = null, cand = null;
    for (const el of candidatesElements) {
      const c = parseSrcset(el.getAttribute('srcset')).find(c => c.url === img.currentSrc);
      if (c) { winner = el; cand = c; break; }
    }
    if (!cand || cand.w === undefined) continue;
    const entry = {i, src: img.currentSrc, w: cand.w, winner: winner.localName,
                   sizes: winner.getAttribute('sizes'), nwBefore: img.naturalWidth};
    report.push(entry);
    pending.push((async () => {
      entry.real = await realWidth(img.currentSrc);
      if (!entry.real) { entry.action = 'probe-failed'; return; }
      if (/\.svg(\?|#|$)/i.test(img.currentSrc)) { entry.action = 'skip-svg'; return; }
      if (Math.abs(entry.real - cand.w) <= 1) { entry.action = 'w-correct'; return; }
      entry.action = 'rewritten';
      const loaded = new Promise(resolve => {
        img.addEventListener('load', resolve, {once: true});
        img.addEventListener('error', resolve, {once: true});
        setTimeout(resolve, 5000);
      });
      winner.setAttribute('srcset', `${img.currentSrc} ${entry.real}w`);
      await loaded;
      entry.nwAfter = img.naturalWidth;
    })());
  }
  await Promise.all(pending);
  return report;
}
"""


async def screenshot(page, path):
    try:
        await page.screenshot(path=path, full_page=True, scale="css", timeout=30000)
        return True
    except Exception:
        try:
            await page.screenshot(path=path, scale="css", timeout=30000)
            return True
        except Exception:
            return False


async def run_page(browser, row, outdir, idx):
    pid = f"{idx:03d}"
    result = {"id": pid, "page": row["page"], "bucket": row["bucket"], "rank": row.get("rank")}
    context = await browser.new_context(**MOBILE, ignore_https_errors=True)
    page = await context.new_page()
    try:
        await page.goto(row["page"], wait_until="load", timeout=60000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        result["A"] = await page.evaluate(MEASURE_JS)
        await screenshot(page, outdir / f"{pid}-A.png")
        await page.wait_for_timeout(2000)
        result["A2"] = await page.evaluate(MEASURE_JS)
        await screenshot(page, outdir / f"{pid}-A2.png")
        result["emulation"] = await page.evaluate(EMULATE_JS)
        await page.wait_for_timeout(2000)
        result["B"] = await page.evaluate(MEASURE_JS)
        await screenshot(page, outdir / f"{pid}-B.png")
        result["status"] = "ok"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e).splitlines()[0][:300]
    finally:
        await context.close()
    return result


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample")
    ap.add_argument("outdir")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int, default=6)
    args = ap.parse_args()

    rows = json.load(open(args.sample))
    if args.limit:
        rows = rows[: args.limit]
    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    results_path = outdir / "results.jsonl"
    done = set()
    if results_path.exists():
        done = {json.loads(l)["page"] for l in results_path.open()}

    sem = asyncio.Semaphore(args.concurrency)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        print("browser", browser.version, flush=True)

        async def worker(idx, row):
            if row["page"] in done:
                return
            async with sem:
                res = await asyncio.wait_for(run_page(browser, row, outdir, idx), timeout=240)
                res["browser"] = browser.version
                with results_path.open("a") as f:
                    f.write(json.dumps(res) + "\n")
                n = sum(1 for e in res.get("emulation", []) if e.get("action") == "rewritten")
                print(idx, res["status"], row["page"], f"rewritten={n}", flush=True)

        await asyncio.gather(
            *(worker(i, r) for i, r in enumerate(rows)), return_exceptions=True
        )
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
