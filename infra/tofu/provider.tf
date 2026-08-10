locals {
  resource_prefix = "${var.project_name}-${var.environment}"
  common_tags = merge(var.extra_tags, {
    Environment = var.environment
    ManagedBy   = "OpenTofu"
    Project     = var.project_name
  })
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}
