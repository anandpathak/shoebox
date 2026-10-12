-- Catalog schema v4: a generated description ("caption") per item, from image analysis (Florence-2).
-- It goes into the full-text index alongside the user's own description, so captions are searchable on
-- every device. A user edit (media.set caption) always beats a generated one (media.caption).
ALTER TABLE media ADD COLUMN caption TEXT;
