output "url" {
  value = "https://${local.hostname}"
}

output "tunnel_id" {
  value = cloudflare_zero_trust_tunnel_cloudflared.server.id
}
