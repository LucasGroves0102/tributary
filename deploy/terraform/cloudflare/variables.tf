variable "zone" {
  description = "Domain registered on Cloudflare."
  type        = string
  default     = "lucasgroves.dev"
}

variable "subdomain" {
  description = "The chat page is served at <subdomain>.<zone>."
  type        = string
  default     = "tributary"
}

variable "tunnel_name" {
  type    = string
  default = "hermes"
}

variable "origin" {
  description = "Where the tunnel sends requests, as seen from cloudflared inside the cluster."
  type        = string
  default     = "http://web.tributary.svc.cluster.local:8080"
}
