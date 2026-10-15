-- Catalog schema v5: words found in the picture (signs, receipts, documents) by on-device text recognition.
-- Part of the full-text index, so "search the receipt from Croma" works on every device.
ALTER TABLE media ADD COLUMN text_in_image TEXT;
