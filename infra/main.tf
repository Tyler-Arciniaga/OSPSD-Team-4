locals {
  # Credentials each provider needs. Terraform does not set them: they are
  # entered by hand in the Render Environment Group named by
  # var.secrets_env_group_id, so their values never reach Terraform state.
  # The service refuses to start if any of these is missing.
  secret_env_var_names = {
    trello = ["TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_LIST_ID"]
    linear = ["LINEAR_API_KEY", "LINEAR_TEAM_ID"]
  }
}

# The service, built by Render from the repository's Dockerfile.
resource "render_web_service" "api" {
  name   = var.service_name
  plan   = "free"
  region = var.region

  # Free services run one instance, which the design requires: the update
  # lock and read cache live in one process (DESIGN.md 5.5).
  num_instances = 1

  # Liveness only. A provider outage must not make Render restart the
  # service, so the provider is not checked here (docs/api.md#health-checks).
  health_check_path = "/healthz"

  runtime_source = {
    docker = {
      repo_url        = var.repo_url
      branch          = var.branch
      dockerfile_path = "./Dockerfile"
      context         = "."

      # Never deploy on push: deploys are manual (docs/DEPLOYMENT.md). Both
      # are set so the plan matches what Render reports back.
      auto_deploy         = false
      auto_deploy_trigger = "off"
    }
  }

  # Configuration only, no secrets. The provider replaces the service's whole
  # variable list on each change, so every service variable is declared here;
  # credentials come from the linked environment group instead.
  env_vars = {
    ISSUE_PROVIDER = { value = var.issue_provider }
    # Render free services have no persistent disk, so keys are lost on
    # restart or redeploy (accepted in DESIGN.md 5.5).
    IDEMPOTENCY_DB_PATH = { value = "/app/data/idempotency.sqlite3" }
  }
}

# Gives the service the credentials in the hand-made environment group.
# This resource's state holds only the two IDs, never the group's values.
resource "render_env_group_link" "secrets" {
  env_group_id = var.secrets_env_group_id
  service_ids  = [render_web_service.api.id]
}
