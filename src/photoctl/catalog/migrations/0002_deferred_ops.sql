-- Ops that reference a photo whose media.upsert hasn't arrived yet (possible when batches from
-- different devices arrive out of order). Re-applied as soon as the photo appears.
CREATE TABLE IF NOT EXISTS deferred_ops (op_id TEXT PRIMARY KEY, photo_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS deferred_ops_photo ON deferred_ops(photo_id);
