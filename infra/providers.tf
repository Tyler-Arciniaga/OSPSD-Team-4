# Credentials come from the environment, never from files in this repository:
#   RENDER_API_KEY   Render API key (secret)
#   RENDER_OWNER_ID  Render workspace ID, tea-... or usr-... (not secret)
provider "render" {
  # By default the provider redeploys a service after any change to it.
  # Deploys instead go through the release workflow, which deploys exactly
  # one image digest and smoke-checks it (docs/DEPLOYMENT.md).
  skip_deploy_after_service_update = true
}
