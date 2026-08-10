variable "project_name" {
  description = "Short project identifier used in resource names and tags."
  type        = string
  default     = "alright-mate"

  validation {
    condition     = can(regex("^[a-z0-9-]{3,32}$", var.project_name))
    error_message = "project_name must contain 3 to 32 lower-case letters, digits or hyphens."
  }
}

variable "environment" {
  description = "Deployment environment identifier."
  type        = string
  default     = "production"

  validation {
    condition     = can(regex("^[a-z0-9-]{2,20}$", var.environment))
    error_message = "environment must contain 2 to 20 lower-case letters, digits or hyphens."
  }
}

variable "aws_region" {
  description = "AWS region for the Seoul deployment."
  type        = string
  default     = "ap-northeast-2"

  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "The MVP is approved only for AWS Seoul (ap-northeast-2)."
  }
}

variable "availability_zone" {
  description = "Seoul availability zone for the Lightsail instance."
  type        = string
  default     = "ap-northeast-2a"

  validation {
    condition     = can(regex("^ap-northeast-2[a-d]$", var.availability_zone))
    error_message = "availability_zone must be an ap-northeast-2 availability zone."
  }
}

variable "lightsail_blueprint_id" {
  description = "Supported Ubuntu LTS Lightsail blueprint."
  type        = string
  default     = "ubuntu_24_04"

  validation {
    condition     = contains(["ubuntu_22_04", "ubuntu_24_04"], var.lightsail_blueprint_id)
    error_message = "lightsail_blueprint_id must select an approved Ubuntu LTS blueprint."
  }
}

variable "lightsail_bundle_id" {
  description = "Supported Lightsail bundle providing 2 GB RAM."
  type        = string
  default     = "small_3_0"

  validation {
    condition     = contains(["small_2_0", "small_3_0"], var.lightsail_bundle_id)
    error_message = "lightsail_bundle_id must select an approved 2 GB Lightsail bundle."
  }
}

variable "operator_ssh_access" {
  description = "Operator networks allowed to reach SSH and the explicit emergency world-access override."
  type = object({
    cidrs           = set(string)
    ipv6_cidrs      = optional(set(string), [])
    allow_world_ssh = optional(bool, false)
  })

  validation {
    condition     = length(var.operator_ssh_access.cidrs) > 0
    error_message = "At least one operator IPv4 CIDR is required."
  }

  validation {
    condition = (
      var.operator_ssh_access.allow_world_ssh ||
      (!contains(var.operator_ssh_access.cidrs, "0.0.0.0/0") && !contains(var.operator_ssh_access.ipv6_cidrs, "::/0"))
    )
    error_message = "World-open SSH is rejected unless allow_world_ssh is deliberately set to true."
  }
}

variable "domain_name" {
  description = "Optional registered domain to manage in Lightsail DNS. Null disables DNS resources."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.domain_name == null ||
      can(regex("^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", var.domain_name))
    )
    error_message = "domain_name must be null or a lower-case fully qualified domain name."
  }
}

variable "dns_record_names" {
  description = "A-record names created in the optional Lightsail DNS zone."
  type        = set(string)
  default     = ["@"]

  validation {
    condition     = length(var.dns_record_names) > 0
    error_message = "dns_record_names must contain at least one record name when DNS is enabled."
  }
}

variable "backup_bucket_name" {
  description = "Globally unique S3 bucket name for encrypted PostgreSQL logical backups."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.backup_bucket_name))
    error_message = "backup_bucket_name must be a valid globally unique S3 bucket name."
  }
}

variable "backup_retention_days" {
  description = "Days to retain current and non-current backup objects."
  type        = number
  default     = 30

  validation {
    condition     = var.backup_retention_days >= 7 && var.backup_retention_days <= 365
    error_message = "backup_retention_days must be between 7 and 365."
  }
}

variable "budget_notification_email" {
  description = "Email address subscribed to the USD 12 and USD 15 monthly budget notifications."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_notification_email))
    error_message = "budget_notification_email must be a syntactically valid email address."
  }
}

variable "extra_tags" {
  description = "Additional non-sensitive tags applied to managed resources."
  type        = map(string)
  default     = {}
}
