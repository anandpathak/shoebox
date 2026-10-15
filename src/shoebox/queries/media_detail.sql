-- One item with everything the viewer shows. :photo_id
SELECT m.*,
  (SELECT json_group_array(tag) FROM (SELECT DISTINCT tag FROM media_tags
     WHERE photo_id = m.photo_id AND add_op NOT IN (SELECT add_op FROM tombstones) ORDER BY tag)) AS tags_json,
  (SELECT json_group_array(album) FROM (SELECT DISTINCT album FROM media_albums
     WHERE photo_id = m.photo_id AND add_op NOT IN (SELECT add_op FROM tombstones) ORDER BY album)) AS albums_json,
  (SELECT json_group_array(json_object('device_id', s.device_id, 'name', d.name, 'platform', d.platform,
                                       'source_ref', s.source_ref))
     FROM media_sources s LEFT JOIN devices d ON d.device_id = s.device_id WHERE s.photo_id = m.photo_id) AS sources_json,
  (SELECT json_group_array(json_object('person_id', mp.person_id, 'add_op', mp.add_op))
     FROM media_people mp WHERE mp.photo_id = m.photo_id
       AND mp.add_op NOT IN (SELECT add_op FROM tombstones)) AS people_json
FROM media m WHERE m.photo_id = :photo_id;
