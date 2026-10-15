# Read-only access for the phone app when no Mac is on (`shoebox viewer`). The key can read thumbnails,
# small copies and the viewer library file only: not originals (raw/), backups, sync data or sidecars,
# and it can't write or delete anything. Phones keep it in the iPhone Keychain / Android Keystore.
# Rotate it (cuts every phone off until they set up again):
#   tofu apply -replace=aws_iam_access_key.reader
# The secret ends up in terraform.tfstate, which stays on your Mac (git-ignored).

data "aws_iam_policy_document" "reader" {
  statement {
    sid     = "ReadPhotosForBrowsing"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.archive.arn}/thumb/*",
      "${aws_s3_bucket.archive.arn}/compressed/*",
      "${aws_s3_bucket.archive.arn}/viewer/*",
    ]
  }
}

resource "aws_iam_user" "reader" {
  name = "${aws_s3_bucket.archive.id}-reader"
}

resource "aws_iam_user_policy" "reader" {
  name   = "read-only-browsing"
  user   = aws_iam_user.reader.name
  policy = data.aws_iam_policy_document.reader.json
}

resource "aws_iam_access_key" "reader" {
  user = aws_iam_user.reader.name
}

# The app's own pages (capacitor://localhost on iPhone, https://localhost on Android) download the library
# file with fetch(), which needs CORS. Pictures load as plain <img> and don't.
resource "aws_s3_bucket_cors_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id
  cors_rule {
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = var.app_origins
    allowed_headers = ["*"]
    expose_headers  = ["ETag", "Content-Length", "Content-Range"]
    max_age_seconds = 3600
  }
}
