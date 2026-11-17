output "service_id" {
  description = "Render service ID (srv-...). Set it as RENDER_SERVICE_ID on the product-production GitHub environment."
  value       = render_web_service.product.id
}

output "service_url" {
  description = "Public URL of the service. Set it as SERVICE_URL on the product-production GitHub environment."
  value       = render_web_service.product.url
}

output "disposable_service_id" {
  description = "Render service ID of the disposable service, or null when it is off. Set it as RENDER_SERVICE_ID on product-disposable."
  value       = one(render_web_service.disposable[*].id)
}

output "disposable_service_url" {
  description = "Public URL of the disposable service, or null when it is off. Set it as SERVICE_URL on product-disposable."
  value       = one(render_web_service.disposable[*].url)
}

output "image_repo" {
  description = "Registry repository the services run images from."
  value       = var.image_repo
}

output "required_secrets" {
  description = "Variables and secret files that must be set by hand in the linked environment group."
  value       = local.secret_names
}
