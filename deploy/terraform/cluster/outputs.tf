output "flux_objects" {
  description = "Number of Flux component objects applied."
  value       = length(kubectl_manifest.components)
}
