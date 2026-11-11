output "service_id" {
  description = "Render service ID (srv-...). Set it as RENDER_SERVICE_ID on the render-production GitHub environment."
  value       = render_web_service.api.id
}

output "service_url" {
  description = "Public URL of the service. Set it as SERVICE_URL on the render-production GitHub environment."
  value       = render_web_service.api.url
}

output "disposable_service_id" {
  description = "Render service ID of the disposable service, or null when it is off. Set it as RENDER_SERVICE_ID on render-disposable."
  value       = one(render_web_service.disposable[*].id)
}

output "disposable_service_url" {
  description = "Public URL of the disposable service, or null when it is off. Set it as SERVICE_URL on render-disposable."
  value       = one(render_web_service.disposable[*].url)
}

output "image_repo" {
  description = "Registry repository the services run images from."
  value       = var.image_repo
}

output "required_secret_env_vars" {
  description = "Variables that must be set by hand in the linked environment group."
  value       = local.secret_env_var_names[var.issue_provider]
}
