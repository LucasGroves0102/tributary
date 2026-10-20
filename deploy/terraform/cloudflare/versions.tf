terraform {
  required_version = ">= 1.9"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.28"
    }
  }
}

# Token from the CLOUDFLARE_API_TOKEN environment variable.
provider "cloudflare" {}
