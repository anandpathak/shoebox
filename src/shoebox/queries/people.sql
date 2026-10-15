-- People that haven't been merged into someone else, with how many photos show them.
SELECT p.person_id, p.name,
  (SELECT COUNT(DISTINCT mp.photo_id) FROM media_people mp JOIN media m ON m.photo_id = mp.photo_id
    WHERE m.deleted = 0 AND mp.add_op NOT IN (SELECT add_op FROM tombstones)
      AND (mp.person_id = p.person_id
           OR mp.person_id IN (SELECT person_id FROM people WHERE merged_into = p.person_id))) AS photos
FROM people p
WHERE p.merged_into IS NULL
ORDER BY photos DESC, p.name;
