# The public entrance: tunnel, its route, DNS, HTTPS settings and the rate limit. cloudflared
# itself runs in the cluster (deploy/flux/cloudflared) with the tunnel token from deploy/secrets.

data "cloudflare_zone" "site" {
  filter = {
    name = var.zone
  }
}

locals {
  account_id = data.cloudflare_zone.site.account.id
  zone_id    = data.cloudflare_zone.site.zone_id
  hostname   = "${var.subdomain}.${var.zone}"
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "server" {
  account_id = local.account_id
  name       = var.tunnel_name
  config_src = "cloudflare"
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "server" {
  account_id = local.account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.server.id
  config = {
    ingress = [
      {
        hostname = local.hostname
        service  = var.origin
      },
      # Anything else that reaches the tunnel gets a 404.
      {
        service = "http_status:404"
      },
    ]
  }
}

resource "cloudflare_dns_record" "app" {
  zone_id = local.zone_id
  name    = local.hostname
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.server.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

resource "cloudflare_zone_setting" "always_use_https" {
  zone_id    = local.zone_id
  setting_id = "always_use_https"
  value      = "on"
}

resource "cloudflare_zone_setting" "min_tls_version" {
  zone_id    = local.zone_id
  setting_id = "min_tls_version"
  value      = "1.2"
}

# Free plan: one rule, per IP and Cloudflare location, 10 s period and block.
resource "cloudflare_ruleset" "rate_limit" {
  zone_id = local.zone_id
  name    = "default"
  kind    = "zone"
  phase   = "http_ratelimit"
  rules = [
    {
      description = "agent and demo login"
      action      = "block"
      expression  = "starts_with(http.request.uri.path, \"/api/agent/\") or http.request.uri.path eq \"/api/query/v1/demo/token\""
      ratelimit = {
        characteristics     = ["ip.src", "cf.colo.id"]
        period              = 10
        requests_per_period = 3
        mitigation_timeout  = 10
      }
    },
  ]
}
