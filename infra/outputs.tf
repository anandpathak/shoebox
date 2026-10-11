output "bucket" {
  description = "Use as PHOTOCTL_BUCKET."
  value       = aws_s3_bucket.archive.id
}

output "region" {
  value = var.region
}

output "ingest_policy_arn" {
  description = "Attach to the identity that runs photoctl."
  value       = aws_iam_policy.ingest.arn
}
