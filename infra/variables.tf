variable "region" {
  description = "AWS region for the archive bucket."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to deploy with (null = default credential chain)."
  type        = string
  default     = null
}

variable "bucket_prefix" {
  description = "Bucket name prefix; a random suffix is appended to make it globally unique."
  type        = string
  default     = "photo-archive"
}

variable "compressed_to_ia_days" {
  description = "Days before compressed/ objects move to Standard-IA."
  type        = number
  default     = 30
}

variable "noncurrent_version_days" {
  description = "Days to keep overwritten/deleted object versions before expiring them."
  type        = number
  default     = 30
}
