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

# Free plan: one rule, per IP and Cloudflare location, 10 s period and block; its expression can
# read only the path (no method), so a route's GETs count with its POSTs. The routes it covers: the
# agent (model spend), "Try the demo" (POST /v1/demo/sandbox creates a user; the sign-in page's GET
# of the offer counts too) and password sign-in (guessing). /v1/demo/token is off in production
# (demo.enabled false) and left out. The Query API keeps its own per-address limits and lockouts
# behind it. A new visitor's sign-in (the offer, the POST, the app's first allowance call) is 3.
resource "cloudflare_ruleset" "rate_limit" {
  zone_id = local.zone_id
  name    = "default"
  kind    = "zone"
  phase   = "http_ratelimit"
  rules = [
    {
      description = "agent, demo sandbox and password sign-in"
      action      = "block"
      expression  = "starts_with(http.request.uri.path, \"/api/agent/\") or http.request.uri.path eq \"/api/query/v1/demo/sandbox\" or http.request.uri.path eq \"/api/query/v1/auth/password\""
      ratelimit = {
        characteristics     = ["ip.src", "cf.colo.id"]
        period              = 10
        requests_per_period = 3
        mitigation_timeout  = 10
      }
    },
  ]
}
