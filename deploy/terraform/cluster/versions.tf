terraform {
  required_version = ">= 1.9"
  required_providers {
    kubectl = {
      source  = "alekc/kubectl"
      version = "~> 2.4"
    }
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
  }
}

provider "kubectl" {
  config_path      = var.kubeconfig
  config_context   = var.kube_context
  load_config_file = true
}
