output "service_id" {
  description = "Render service ID (srv-...), used by the deploy step."
  value       = render_web_service.api.id
}

output "service_url" {
  description = "Public URL of the service."
  value       = render_web_service.api.url
}

# Not sensitive, so a commit change is visible in every plan.
output "commit_sha" {
  description = "Commit the deploy step deploys and /readyz must report."
  value       = var.commit_sha
}

output "required_secret_env_vars" {
  description = "Variables that must be set by hand in the linked environment group."
  value       = local.secret_env_var_names[var.issue_provider]
}
