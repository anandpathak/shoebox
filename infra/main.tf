resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "archive" {
  bucket = "${var.bucket_prefix}-${random_id.suffix.hex}"

  # Originals live here. Deleting the bucket must be a deliberate, manual act.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "archive" {
  bucket                  = aws_s3_bucket.archive.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "archive" {
  bucket = aws_s3_bucket.archive.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "archive" {
  bucket = aws_s3_bucket.archive.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "archive" {
  bucket     = aws_s3_bucket.archive.id
  depends_on = [aws_s3_bucket_versioning.archive]

  # raw/ needs no transition rule: photoctl uploads originals straight to DEEP_ARCHIVE.

  rule {
    id     = "compressed-to-ia"
    status = "Enabled"
    filter {
      prefix = "compressed/"
    }
    transition {
      days          = var.compressed_to_ia_days
      storage_class = "STANDARD_IA"
    }
  }

  # Sync change batches. New devices bootstrap from sync/snapshots/latest.sqlite.gz (refreshed at
  # least every 30 days by whichever device holds the compaction lease), so old batches can expire.
  rule {
    id     = "sync-ops-expiry"
    status = "Enabled"
    filter {
      prefix = "sync/ops/"
    }
    expiration {
      days = var.sync_ops_retention_days
    }
  }

  rule {
    id     = "housekeeping"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.archive.arn,
      "${aws_s3_bucket.archive.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "archive" {
  bucket     = aws_s3_bucket.archive.id
  policy     = data.aws_iam_policy_document.bucket.json
  depends_on = [aws_s3_bucket_public_access_block.archive]
}

# Least-privilege policy for whoever runs `photoctl`: read/write/restore, but no delete.
# Attach it to your SSO permission set, IAM user or role.
data "aws_iam_policy_document" "ingest" {
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.archive.arn]
  }
  statement {
    sid = "ReadWriteObjects"
    # Covers raw/, compressed/, thumb/, meta/, sync/ (ops, snapshots, leases, catalogs, device registry)
    # and backups/. No s3:DeleteObject: leases and pointers are replaced with conditional PUTs instead.
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:GetObjectAttributes",
      "s3:RestoreObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.archive.arn}/*"]
  }
}

resource "aws_iam_policy" "ingest" {
  name        = "${aws_s3_bucket.archive.id}-ingest"
  description = "photoctl: upload/read/restore in the photo archive bucket; no deletes."
  policy      = data.aws_iam_policy_document.ingest.json
}
