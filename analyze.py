"""Summarize emulate.py output.

Usage: python analyze.py out/ > summary.md   (also writes out/pages.csv and out/review/*.png)

Per page:
- rewritten: loaded <img>s whose selected `w` descriptor was wrong and got rewritten.
- changed: rewritten <img>s whose rendered box (width or height) changed by more than 1px
  between the second baseline (A2) and after emulation (B).
- noise: <img>s (any) whose box changed by more than 1px between the two baselines (A, A2).
- shot_noise / shot_diff: fraction of differing pixels in the viewport-width full-page screenshot,
  A vs A2 and A2 vs B.
"""

import csv
import json
import pathlib
import sys

from PIL import Image, ImageChops, ImageDraw


def box_changed(a, b, tol=1):
    return abs(a["w"] - b["w"]) > tol or abs(a["h"] - b["h"]) > tol


def diff_fraction(p1, p2):
    try:
        a = Image.open(p1).convert("RGB")
        b = Image.open(p2).convert("RGB")
    except Exception:
        return None
    w = max(a.width, b.width)
    h = max(a.height, b.height)
    ca = Image.new("RGB", (w, h), "white")
    ca.paste(a)
    cb = Image.new("RGB", (w, h), "white")
    cb.paste(b)
    bbox_diff = ImageChops.difference(ca, cb).convert("L").point(lambda v: 255 if v > 16 else 0)
    hist = bbox_diff.histogram()
    return hist[255] / (w * h)


def side_by_side(p1, p2, out, label, top=0, bottom=6000):
    """Before/after pair, cropped to [top, bottom) in CSS px."""
    a = Image.open(p1).convert("RGB")
    b = Image.open(p2).convert("RGB")
    top = max(0, int(top))
    bottom = int(min(bottom, max(a.height, b.height)))
    h = bottom - top
    img = Image.new("RGB", (a.width + b.width + 20, h + 30), "white")
    img.paste(a.crop((0, top, a.width, bottom)), (0, 30))
    img.paste(b.crop((0, top, b.width, bottom)), (a.width + 20, 30))
    d = ImageDraw.Draw(img)
    d.text((4, 8), "before", fill="black")
    d.text((a.width + 24, 8), "after (emulated)  " + label, fill="black")
    img.save(out)


def main():
    outdir = pathlib.Path(sys.argv[1])
    review = outdir / "review"
    review.mkdir(exist_ok=True)
    rows = []
    for line in (outdir / "results.jsonl").open():
        r = json.loads(line)
        row = {"id": r["id"], "page": r["page"], "bucket": r["bucket"], "status": r["status"]}
        if r["status"] == "ok":
            em = r["emulation"]
            rewritten = [e for e in em if e.get("action") == "rewritten"]
            A, A2, B = r["A"], r["A2"], r["B"]
            byi = lambda snap: {x["i"]: x for x in snap["imgs"]}
            a, a2, b = byi(A), byi(A2), byi(B)
            changed = [
                e for e in rewritten
                if e["i"] in a2 and e["i"] in b and box_changed(a2[e["i"]], b[e["i"]])
            ]
            visible_changed = [
                e for e in changed
                if a2[e["i"]]["w"] > 0 or b[e["i"]]["w"] > 0
            ]
            noise = sum(1 for i in a if i in a2 and box_changed(a[i], a2[i]))
            others_changed = sum(
                1 for i in a2 if i in b and box_changed(a2[i], b[i])
                and i not in {e["i"] for e in rewritten}
            )
            max_dw = max(
                (abs(b[e["i"]]["w"] - a2[e["i"]]["w"]) for e in changed), default=0
            )
            row.update(
                w_imgs=len(em),
                rewritten=len(rewritten),
                changed=len(visible_changed),
                max_dw=round(max_dw),
                others_changed=others_changed,
                noise=noise,
                height_A=A["scrollHeight"],
                height_A2=A2["scrollHeight"],
                height_B=B["scrollHeight"],
                width_B=B["scrollWidth"],
                width_A2=A2["scrollWidth"],
            )
            pa, pa2, pb = (outdir / f"{r['id']}-{s}.png" for s in ("A", "A2", "B"))
            row["shot_noise"] = diff_fraction(pa, pa2)
            row["shot_diff"] = diff_fraction(pa2, pb)
            if visible_changed and pa2.exists() and pb.exists():
                first = min(visible_changed, key=lambda e: a2[e["i"]]["y"])
                y0 = min(a2[first["i"]]["y"], b[first["i"]]["y"])
                y1 = max(a2[first["i"]]["y"] + a2[first["i"]]["h"], b[first["i"]]["y"] + b[first["i"]]["h"])
                side_by_side(pa2, pb, review / f"{r['id']}.png", r["page"],
                             y0 - 250, min(y1 + 250, y0 + 1400))
        else:
            row["error"] = r.get("error")
        rows.append(row)

    fields = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
    with (outdir / "pages.csv").open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        wr.writerows(sorted(rows, key=lambda r: r["id"]))

    def count(bucket, pred):
        return sum(1 for r in rows if r["bucket"] == bucket and pred(r))

    cats = [
        ("load error", lambda r: r["status"] != "ok"),
        ("no wrong `w` on a loaded image live (not reproduced)",
         lambda r: r["status"] == "ok" and r["rewritten"] == 0),
        ("wrong `w` rewritten, no rendered box changed",
         lambda r: r["status"] == "ok" and r["rewritten"] > 0 and r["changed"] == 0),
        ("wrong `w` rewritten, rendered box changed",
         lambda r: r["status"] == "ok" and r["changed"] > 0),
    ]
    print("| Outcome | definite | maybe | total |")
    print("|---|---|---|---|")
    for name, pred in cats:
        d, m = count("definite", pred), count("maybe", pred)
        print(f"| {name} | {d} | {m} | {d + m} |")


if __name__ == "__main__":
    main()
