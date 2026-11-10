output "bucket_name" {
  description = "Name of the private S3 bucket (no objects are uploaded by Terraform)."
  value       = aws_s3_bucket.data_lake.bucket
}

output "aws_region" {
  description = "Region of the bucket."
  value       = var.aws_region
}

output "data_lake_prefixes" {
  description = "Logical prefixes to use when we deliberately upload datasets later."
  value       = ["bronze/", "silver/", "gold/"]
}
