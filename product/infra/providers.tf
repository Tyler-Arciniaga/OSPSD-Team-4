# Credentials come from the environment, never from files in this repository:
#   RENDER_API_KEY   Render API key (secret)
#   RENDER_OWNER_ID  Render workspace ID, tea-... or usr-... (not secret)
provider "render" {
  # Deploys go through the product release workflow
  # (.github/workflows/release-product.yml), which deploys exactly one image
  # digest and smoke-checks it.
  skip_deploy_after_service_update = true
}
