provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = "AegisData-AI"
      Environment = "portfolio-demo"
      ManagedBy   = "Terraform"
      Owner       = "personal-project"
    }
  }
}

data "aws_caller_identity" "current" {}
