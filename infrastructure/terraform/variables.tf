variable "aws_region" {
  description = "AWS region for the portfolio S3 bucket."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Dedicated, browser-authenticated AWS CLI profile."
  type        = string
  default     = "aegisdata"
}

variable "expected_account_id" {
  description = "Account safety check: set to the 12-digit AWS account ID you verified with STS."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must contain exactly 12 digits."
  }
}
