variable "kubeconfig" {
  description = "Kubeconfig for the k3s cluster on the server."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Context in the kubeconfig; null uses the current one."
  type        = string
  default     = null
}
