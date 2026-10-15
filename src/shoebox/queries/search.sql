-- Library search, shared by the Python server/CLI and the TypeScript app (sqlite-wasm).
-- Every parameter is optional (NULL = no filter). Build :fts with build_fts() (spec/query_vectors.json).
--   :fts            FTS5 expression over title, description, tags, people, place, orig_name, albums
--   :date_from      ISO date/time, inclusive        :date_to   ISO date/time, exclusive
--   :month          '01'..'12' in any year          :media_type  'photo' | 'video'
--   :favorite       1 = favourites only             :include_hidden  1 = also hidden items
--   :device         device_id, platform ('ios', 'android', 'macos'…) or device name
--   :cursor_taken, :cursor_id   keyset pagination: values from the last row of the previous page
--   :limit          page size
SELECT m.photo_id, m.media_type, m.taken_at, m.title, m.favorite, m.hidden, m.duration_s,
       m.width, m.height, m.place_name, m.place_city, m.place_region, m.place_country,
       m.thumb_key, m.compressed_key, m.poster_key, m.raw_key, m.meta_key, m.bytes_raw, m.bytes_compressed,
       (SELECT group_concat(DISTINCT s.device_id) FROM media_sources s WHERE s.photo_id = m.photo_id) AS devices
FROM media m
WHERE m.deleted = 0
  AND (:include_hidden = 1 OR m.hidden = 0)
  AND (:fts IS NULL OR m.photo_id IN (SELECT photo_id FROM media_fts WHERE media_fts MATCH :fts))
  AND (:date_from IS NULL OR m.taken_at >= :date_from)
  AND (:date_to IS NULL OR m.taken_at < :date_to)
  AND (:month IS NULL OR substr(m.taken_at, 6, 2) = :month)
  AND (:media_type IS NULL OR m.media_type = :media_type)
  AND (:favorite IS NULL OR m.favorite = :favorite)
  AND (:device IS NULL OR m.photo_id IN (
        SELECT s.photo_id FROM media_sources s LEFT JOIN devices d ON d.device_id = s.device_id
        WHERE s.device_id = :device OR d.platform = lower(:device) OR lower(d.name) = lower(:device)))
  AND (:cursor_taken IS NULL OR (COALESCE(m.taken_at, ''), m.photo_id) < (:cursor_taken, :cursor_id))
ORDER BY COALESCE(m.taken_at, '') DESC, m.photo_id DESC
LIMIT :limit;
