output "bucket" {
  description = "Use as SHOEBOX_BUCKET."
  value       = aws_s3_bucket.archive.id
}

output "region" {
  value = var.region
}

output "ingest_policy_arn" {
  description = "Attach to the identity that runs shoebox."
  value       = aws_iam_policy.ingest.arn
}

output "reader_key_id" {
  description = "Read-only key for phones: shoebox viewer access --key-id <this>"
  value       = aws_iam_access_key.reader.id
}

output "reader_secret" {
  description = "Its secret: tofu output -raw reader_secret | shoebox viewer access --key-id … --secret-stdin"
  value       = aws_iam_access_key.reader.secret
  sensitive   = true
}
