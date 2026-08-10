resource "aws_lightsail_domain" "this" {
  count       = var.domain_name == null ? 0 : 1
  domain_name = var.domain_name
}

resource "aws_lightsail_domain_entry" "this" {
  for_each = var.domain_name == null ? toset([]) : var.dns_record_names

  domain_name = aws_lightsail_domain.this[0].domain_name
  name        = each.value
  type        = "A"
  target      = aws_lightsail_static_ip.this.ip_address
}
