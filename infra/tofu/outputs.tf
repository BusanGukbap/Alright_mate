output "lightsail_instance_name" {
  description = "Name of the provisioned Lightsail instance."
  value       = aws_lightsail_instance.this.name
}

output "static_ipv4_address" {
  description = "Static public IPv4 address to place in the example Ansible inventory."
  value       = aws_lightsail_static_ip.this.ip_address
}

output "backup_bucket_name" {
  description = "Name of the encrypted and versioned PostgreSQL backup bucket."
  value       = aws_s3_bucket.backups.bucket
}

output "lightsail_dns_domain" {
  description = "Managed Lightsail DNS domain, or null when DNS is disabled."
  value       = var.domain_name
}
