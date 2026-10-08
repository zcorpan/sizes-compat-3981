WITH imgs AS (
  SELECT client, page, rank, img
  FROM `httparchive.crawl.pages`,
    UNNEST(JSON_QUERY_ARRAY(custom_metrics.responsive_images, '$."responsive-images"')) AS img
  WHERE date = '2026-09-01' AND is_root_page
),
w AS (
  SELECT
    client, page, rank,
    SAFE.INT64(img.naturalWidth) AS natural_w,
    SAFE.INT64(img.clientWidth) AS client_w,
    SAFE.FLOAT64(img.sizesWidth) AS sizes_w,
    STRING(img.sizesCSSLength) AS sizes_css,
    SAFE.FLOAT64(img.currentSrcWDescriptor) AS cur_w,
    SAFE.FLOAT64(img.wDescriptorRelativeError) AS w_err,
    STRING(img.intrinsicOrExtrinsicSizing.width) AS sizing_w,
    IFNULL(BOOL(img.srcsetHasWDescriptors), FALSE) AS has_w,
    IFNULL(LAX_BOOL(img.sizesParseError), FALSE) AS sizes_parse_error,
    IFNULL(BOOL(img.hasWidth), FALSE) AND IFNULL(BOOL(img.hasHeight), FALSE) AS has_dims
  FROM imgs
),
c AS (
  SELECT
    *,
    has_w AND natural_w > 0 AND cur_w IS NOT NULL AND sizes_w > 0 AND sizes_css != 'auto'
      -- the metric can't resolve e.g. `sizes="auto, ..."`; its sizes value (and w error) is unreliable
      AND NOT sizes_parse_error
      AND ABS(sizes_w - natural_w) > 1 AND ABS(w_err) > 0.01 AS mismatch,
    -- estimated new rendered width; NULL = can't tell (would grow, constraint unknown)
    CASE
      WHEN sizing_w = 'extrinsic' THEN client_w
      WHEN sizing_w = 'intrinsic' THEN sizes_w
      -- 'both': auto width with min/max constraints
      WHEN client_w < natural_w - 1 THEN IF(sizes_w < client_w, sizes_w, client_w)  -- max-clamped
      WHEN client_w > natural_w + 1 THEN IF(sizes_w > client_w, NULL, client_w)     -- min-clamped
      WHEN sizes_w < natural_w THEN NULL  -- shrinks unless a min constraint kicks in
      ELSE NULL                           -- grows unless a max constraint kicks in
    END AS new_client_w
  FROM w
),
p AS (
  SELECT
    client,
    page,
    ANY_VALUE(rank) AS rank,
    LOGICAL_OR(has_w) AS has_w,
    LOGICAL_OR(mismatch) AS mismatch,
    LOGICAL_OR(mismatch AND client_w > 0 AND new_client_w IS NOT NULL AND ABS(new_client_w - client_w) > 1) AS mismatch_layout,
    LOGICAL_OR(mismatch AND client_w > 0 AND new_client_w IS NULL) AS mismatch_layout_maybe,
    LOGICAL_OR(mismatch AND client_w > 0
      AND new_client_w IS NOT NULL AND ABS(new_client_w - client_w) > GREATEST(10, 0.1 * client_w)) AS mismatch_layout_big,
    LOGICAL_OR(has_w AND IFNULL(natural_w, 0) = 0 AND sizing_w != 'extrinsic') AS notloaded_auto_width,
    LOGICAL_OR(has_w AND IFNULL(natural_w, 0) = 0 AND sizing_w != 'extrinsic' AND NOT has_dims) AS notloaded_auto_width_no_dims
  FROM c
  GROUP BY client, page
)
SELECT
  client,
  IFNULL(CAST(rank AS STRING), 'all') AS rank_bucket,
  COUNT(*) AS pages,
  COUNTIF(has_w) AS has_w,
  COUNTIF(mismatch) AS mismatch,
  COUNTIF(mismatch_layout) AS mismatch_layout,
  COUNTIF(mismatch_layout_big) AS mismatch_layout_big,
  COUNTIF(mismatch_layout_maybe AND NOT mismatch_layout) AS mismatch_layout_maybe_only,
  COUNTIF(notloaded_auto_width) AS notloaded_auto_width,
  COUNTIF(notloaded_auto_width_no_dims) AS notloaded_auto_width_no_dims
FROM p
GROUP BY ROLLUP(client, rank)
ORDER BY client, rank
