-- Devices known to the catalog and how many items each contributed.
SELECT d.device_id, d.name, d.platform, d.first_seen_hlc, d.last_seen_hlc,
  (SELECT COUNT(DISTINCT s.photo_id) FROM media_sources s JOIN media m ON m.photo_id = s.photo_id
    WHERE s.device_id = d.device_id AND m.deleted = 0) AS items
FROM devices d ORDER BY d.name;
