# Installs Flux and points it at this repository; Flux then applies everything under
# deploy/flux (namespaces, SOPS secrets, Helm releases, image automation, the tunnel).
#
# The two bootstrap secrets are created on the server beforehand, so no private key is ever in
# Terraform state: flux-system (repository deploy key) and sops-age (decrypts deploy/secrets).

locals {
  flux_dir = "${path.module}/../../flux/clusters/hermes/flux-system"
}

data "kubectl_file_documents" "components" {
  content = file("${local.flux_dir}/gotk-components.yaml")
}

data "kubectl_file_documents" "sync" {
  content = file("${local.flux_dir}/gotk-sync.yaml")
}

resource "kubectl_manifest" "components" {
  for_each          = data.kubectl_file_documents.components.manifests
  yaml_body         = each.value
  server_side_apply = true
  wait              = true
}

# Existence only: reading the secrets would copy the keys into Terraform state.
data "external" "bootstrap_secrets" {
  program = [
    "sh", "-c", <<-EOT
      for s in flux-system sops-age; do
        if kubectl --kubeconfig "$1" $${2:+--context "$2"} -n flux-system get secret "$s" -o name >/dev/null 2>&1
        then printf '"%s":"true"\n' "$s"; else printf '"%s":"false"\n' "$s"; fi
      done | paste -sd, - | sed 's/^/{/; s/$/}/'
    EOT
    , "sh", pathexpand(var.kubeconfig), var.kube_context == null ? "" : var.kube_context
  ]
  depends_on = [kubectl_manifest.components]
}

resource "kubectl_manifest" "sync" {
  for_each          = data.kubectl_file_documents.sync.manifests
  yaml_body         = each.value
  server_side_apply = true

  lifecycle {
    precondition {
      condition     = alltrue([for v in values(data.external.bootstrap_secrets.result) : v == "true"])
      error_message = "Create the flux-system and sops-age secrets in flux-system first (see README)."
    }
  }
}
