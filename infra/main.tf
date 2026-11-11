locals {
  # Credentials each provider needs. Terraform does not set them: they are
  # entered by hand in the Render Environment Group named by
  # var.secrets_env_group_id, so their values never reach Terraform state.
  # The service refuses to start if any of these is missing.
  secret_env_var_names = {
    trello = ["TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_LIST_ID"]
    linear = ["LINEAR_API_KEY", "LINEAR_TEAM_ID"]
  }

  # Configuration only, no secrets. The provider replaces a service's whole
  # variable list on each change, so every service variable is declared here;
  # credentials come from the linked environment group instead.
  service_env_vars = {
    ISSUE_PROVIDER = { value = var.issue_provider }
    # Render free services have no persistent disk, so keys are lost on
    # restart or redeploy (accepted in DESIGN.md 5.5).
    IDEMPOTENCY_DB_PATH = { value = "/app/data/idempotency.sqlite3" }
  }
}

# The service, running the image the release workflow built and pushed.
resource "render_web_service" "api" {
  name   = var.service_name
  plan   = "free"
  region = var.region

  # Free services always run one instance, which the design requires: the
  # update lock and read cache live in one process (DESIGN.md 5.5).
  # num_instances is left unset on purpose: when it is set, provider 1.9.1
  # calls Render's scale endpoint on every update, and Render refuses that on
  # the free plan ("Plan requires payment information on file").

  # Liveness only. A provider outage must not make Render restart the
  # service, so the provider is not checked here (docs/api.md#health-checks).
  health_check_path = "/healthz"

  # Always pinned by digest, never a tag. Which digest runs is the release
  # workflow's job (scripts/release.py deploy), not Terraform's: Terraform
  # sets the digest only when it first creates or converts the service, and
  # ignores later changes, so a plan after a release shows no changes.
  runtime_source = {
    image = {
      image_url = var.image_repo
      digest    = var.bootstrap_image_digest
    }
  }

  env_vars = local.service_env_vars

  lifecycle {
    ignore_changes = [runtime_source.image.digest]
  }
}

# A second, temporary service for demonstrating a faulty release and a
# rollback without touching production (docs/DEPLOYMENT.md). Off by default;
# the release workflow deploys to it with target "disposable".
resource "render_web_service" "disposable" {
  count = var.disposable_enabled ? 1 : 0

  name              = "${var.service_name}-disposable"
  plan              = "free"
  region            = var.region
  health_check_path = "/healthz" # num_instances unset, as for api above

  runtime_source = {
    image = {
      image_url = var.image_repo
      digest    = var.bootstrap_image_digest
    }
  }

  env_vars = local.service_env_vars

  lifecycle {
    ignore_changes = [runtime_source.image.digest]
  }
}

# Gives the services the credentials in the hand-made environment group, so
# the disposable service uses the same Trello test list as production.
# This resource's state holds only IDs, never the group's values.
resource "render_env_group_link" "secrets" {
  env_group_id = var.secrets_env_group_id
  service_ids  = concat([render_web_service.api.id], render_web_service.disposable[*].id)
}
