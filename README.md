# Compat impact of making `sizes` set the natural width (whatwg/html#3981)

Proposed change: when an `img`'s selected image source has a `w` descriptor, its density-corrected
natural width is the source size (`sizes`), with the height scaled to match, instead of
`resourceWidth × sizes / w`. The two only differ when the `w` descriptor doesn't match the
resource's real width. A second part gives a still-loading `img` selected that way a natural width
equal to the source size; only the HTTP Archive exposure numbers below cover that part.

## Summary

- 37% of mobile root pages use `w` descriptors; 3.1% have at least one loaded image with a wrong
  `w`. In most of those, CSS sizes the image anyway, so only `naturalWidth` changes.
- Estimated from HTTP Archive, 0.046% of mobile root pages (6,650) render a wrong-`w` image at a
  different width, and up to another 0.090% (13,103) might.
- Emulating the rule in Chromium on 200 crawl-flagged pages, 23 pages rendered differently. By
  manual review: 1 broken, 2 worse, 12 neutral (image grows or shrinks to the size `sizes` asks
  for), 1 improved, 4 with no visible change, 3 not assessable. The breakage pattern is
  `srcset="icon@2x.png 2w" sizes="1px"` to fake `2x`; HTTP Archive has 48 mobile and 37 desktop
  root pages using it with a wrong `w`.
- Loading-state part (not emulated): on 2.7% of mobile root pages, a `w`-selected `img` with a
  non-fixed CSS width wasn't loaded when the crawl measured (likely lazy-loaded); 0.84% of pages
  have such an image without `width`/`height` attributes.

## 1. HTTP Archive estimate

`impact.sql` runs over the 2026-09-01 crawl (`httparchive.crawl.pages`, root pages, both clients)
using the `responsive_images` custom metric
([source](https://github.com/HTTPArchive/custom-metrics/blob/49df8e73a3db3c4c818791728348fd82061a7ae7/dist/responsive_images.js)),
which Chrome collects after load (the mobile viewport is 360px wide). Per `img`:

- *wrong `w`*: the selected candidate has a `w` descriptor; `naturalWidth` differs from the
  metric's resolved `sizes` by more than 1px; the `w` descriptor is more than 1% off from the
  metric's resource width (`wDescriptorRelativeError`); and the metric parsed `sizes` without error.
  The last condition matters: the metric doesn't support `sizes="auto, ..."` (added by WordPress
  to lazy images) and falls back to the next entry, typically `100vw`, which makes `w` look wrong
  when it isn't. With `sizes=auto`, the new natural width is the laid-out width anyway.
- *definite* layout change: wrong `w`, rendered (`clientWidth > 0`), and the estimated new rendered
  width differs by more than 1px. The estimate uses the computed `width`/`min-width`/`max-width`
  (`intrinsicOrExtrinsicSizing`): a non-`auto` width doesn't change; an `auto` width with no
  min/max becomes `sizes`; an `auto` width clamped by `max-width` becomes `min(sizes, clientWidth)`.
- *maybe*: wrong `w`, rendered, `auto` width with min/max constraints where the outcome can't be
  told from the metric.
- *not loaded, auto width*: an `img` with `w` descriptors, `naturalWidth` 0, and a non-fixed CSS
  width; exposure to the loading-state part.

Results in `impact-results.csv` (pages per client and CrUX rank bucket; buckets aren't cumulative):

| | mobile | desktop |
|---|---|---|
| Root pages with an `img` | 14,605,919 | 10,769,553 |
| Use `w` descriptors | 5,412,532 (37%) | 4,077,869 (38%) |
| Wrong `w` | 456,081 (3.1%) | 288,466 (2.7%) |
| Definite layout change | 6,650 (0.046%) | 4,327 (0.040%) |
| …by more than 10% and 10px | 5,965 (0.041%) | 3,880 (0.036%) |
| Maybe (and not definite) | 13,103 (0.090%) | 3,954 (0.037%) |
| Not loaded, auto width | 394,098 (2.7%) | 200,151 (1.9%) |
| …without `width` and `height` attributes | 122,129 (0.84%) | 77,148 (0.72%) |

In the top 10k sites (mobile, 6,901 pages), see the `1000`, `5000`, and `10000` rows.

`tiny-sizes.sql` counts pages with a wrong-`w` image whose `sizes` resolves to 2px or less but that
renders wider than 2px with a non-fixed CSS width (the `sizes="1px"` trick, which would render at
about 1px): 48 mobile and 37 desktop root pages (`tiny-sizes-results.json`, with examples).

## 2. Browser emulation on a sample

`sample.sql` picks 100 *definite* and 100 *maybe* mobile pages, ordered by
`FARM_FINGERPRINT(CONCAT(page, '#3981'))` (`sample.json`). It was run before the `sizesParseError`
exclusion was added to `impact.sql`, so it includes the `sizes="auto, ..."` false positives.

`emulate.py` loads each page in Playwright's Chromium (headless shell 153.0.8010.12) with the
HTTP Archive mobile setup (360×640 viewport, DPR 3, mobile UA, touch), waits for `load`, network
idle (max 10s), and 2s, then:

1. **A**: records every `img`'s box, natural size, and `currentSrc`, and the document's scroll
   size; full-page screenshot at CSS-pixel scale.
2. **A2**: the same 2s later, without changes, to separate page noise (carousels, animations).
3. **Emulation**: for each loaded `img`, finds the element (`img` or a `picture`'s `source`) whose
   `srcset` contains `currentSrc`. If that candidate has a `w` descriptor, it loads `currentSrc`
   in a new `Image()` to get the real width; if `w` is more than 1px off, it sets that element's
   `srcset` to `"<currentSrc> <realWidth>w"`. URL and `sizes` stay the same, so the natural width
   becomes `realWidth × sizes / realWidth = sizes`, which is what the proposed rule gives. SVGs
   are skipped.
4. **B**: records and screenshots again after the rewritten images reload (from cache) and 2s.

`analyze.py` compares A2 with B (`out/pages.csv`, `out/summary.md`) and writes before/after pairs,
cropped around the first changed image, for pages where a rewritten image's box changed by more
than 1px (`out/review/`). I reviewed each pair by hand (`out/review.csv`). Raw measurements are in
`out/results.jsonl`.

| Outcome | definite | maybe | total |
|---|---|---|---|
| Load error | 1 | 1 | 2 |
| No wrong `w` on a loaded image live (not reproduced) | 34 | 75 | 109 |
| Wrong `w` rewritten, no rendered box changed | 57 | 9 | 66 |
| Wrong `w` rewritten, rendered box changed | 8 | 15 | 23 |

Of the 109 not reproduced, 99 had only correct `w` descriptors live; spot checks show
`sizes="auto, ..."`, the metric limitation described above. In the pages I checked where nothing
changed, CSS sized the images some other way or they weren't rendered.

Manual review of the 23 changed pages:

| Verdict | definite | maybe | total |
|---|---|---|---|
| Broken | 1 | 0 | 1 |
| Worse | 1 | 1 | 2 |
| Neutral | 1 | 11 | 12 |
| Improved | 1 | 0 | 1 |
| No visible change | 4 | 0 | 4 |
| Not assessable (consent dialog, blank page) | 0 | 3 | 3 |

Extrapolation to all 14,605,919 mobile root pages, weighting each stratum by its size in the run
`sample.sql` drew from (definite 11,215, maybe 62,796; `impact-results-sampling-frame.csv`, which
is `impact.sql` without the `sizesParseError` exclusion) and assuming unflagged pages are
unaffected. The intervals are rough: exact binomial 95% intervals per stratum, summed.

| Verdict | Estimated pages | Share | Rough 95% interval |
|---|---|---|---|
| Broken | 112 | 0.0008% | 0.00002%–0.020% |
| Worse | 740 | 0.0051% | 0.0001%–0.028% |
| Improved | 112 | 0.0008% | 0.00002%–0.020% |
| Neutral | 7,020 | 0.048% | 0.024%–0.085% |

- Broken: doucujte.cz uses `srcset="icon@2x.png 2w" sizes="1px"`; the icons render 1×1.
- Worse: kral-buch.at has no `sizes` and `1200w` on every image regardless of real size, so a 45px
  portrait becomes 360px wide; memoiresdeladistribution.fr's logos grow past their card.

## Not covered

- The loading-state part, beyond the exposure numbers.
- Images that weren't loaded when measured (e.g. lazy-loaded below the fold).
- Non-root pages, desktop in the emulation, viewports other than 360px, and engines other than
  Chromium.
- Interaction (e.g. dismissing consent dialogs or scrolling).

## Query cost

All on the 2026-09-01 crawl, run on-demand on 2026-10-08:

| Query | Bytes billed |
|---|---|
| `impact.sql` (run twice: before and after adding the `sizesParseError` exclusion) | 699.5 GB each |
| `sample.sql` | 389.2 GB |
| `tiny-sizes.sql` (run twice: without and with the wrong-`w` condition) | 699.3 GB each |
| Exploratory queries on a 1% `TABLESAMPLE` (not included here) | 4 × ~4 GB |

Total: 3.2 TB (2.9 TiB), about $18 at the $6.25/TiB on-demand list price. A single rerun of
`impact.sql`, `sample.sql`, and `tiny-sizes.sql` scans about 1.8 TB.

## Reproduce

```sh
bq query --use_legacy_sql=false --format=csv < impact.sql > impact-results.csv
bq query --use_legacy_sql=false --format=prettyjson < tiny-sizes.sql > tiny-sizes-results.json
bq query --use_legacy_sql=false --format=json --max_rows=1000 < sample.sql > sample.json
python3 -m venv .venv && .venv/bin/pip install playwright pillow && .venv/bin/playwright install chromium
.venv/bin/python emulate.py sample.json out
.venv/bin/python analyze.py out > out/summary.md
```

Live pages change, so a rerun of the emulation won't match exactly. Full-page screenshots
(`out/NNN-{A,A2,B}.png`, 632 MB) aren't committed; `emulate.py` regenerates them.
