locals {
  # Set by hand in the environment group named by var.secrets_env_group_id,
  # so their values never reach Terraform state. The service refuses to start
  # if the calendar token file is missing or unreadable, or if Dropbox cannot
  # be built without its credentials.
  secret_names = {
    env_vars = [
      "TRELLO_API_KEY",
      "TRELLO_TOKEN",
      "TRELLO_LIST_ID",
      "DROPBOX_APP_KEY",
      "DROPBOX_APP_SECRET",
      "DROPBOX_REFRESH_TOKEN",
    ]
    secret_files = [var.calendar_token_file_name]
  }

  # Configuration only, no secrets. The provider replaces a service's whole
  # variable list on each change, so every service variable is declared here.
  production_env_vars = {
    TRACKER_PROVIDER    = { value = "team_4_issue_tracker_trello:TrelloIssueProvider" }
    CALENDAR_PROVIDER   = { value = "team5_calendar:create_calendar_service" }
    STORAGE_PROVIDER    = { value = "dropbox_client_impl:DropboxDocumentStorage" }
    CALENDAR_TOKEN_FILE = { value = "/etc/secrets/${var.calendar_token_file_name}" }
    DEFAULT_TIMEZONE    = { value = var.default_timezone }
  }

  # The disposable service selects the in-memory providers through the same
  # variables. It needs no credentials and is not linked to the secrets.
  disposable_env_vars = {
    TRACKER_PROVIDER  = { value = "ospsd_team4_product.fakes:InMemoryIssueProvider" }
    CALENDAR_PROVIDER = { value = "ospsd_team4_product.fakes:InMemoryCalendarService" }
    STORAGE_PROVIDER  = { value = "ospsd_team4_product.fakes:InMemoryDocumentStorage" }
    DEFAULT_TIMEZONE  = { value = var.default_timezone }
  }
}

# The product service, running the image the product release workflow built.
resource "render_web_service" "product" {
  name   = var.service_name
  plan   = "free"
  region = var.region

  # num_instances is left unset, as for HW2: provider 1.9.1 would call
  # Render's scale endpoint on every update, which the free plan refuses.

  # Liveness only: a partner outage must not make Render restart the service.
  health_check_path = "/healthz"

  # Pinned by digest. Terraform sets it only when it creates the service;
  # releases change it afterwards (scripts/release.py deploy).
  runtime_source = {
    image = {
      image_url = var.image_repo
      digest    = var.bootstrap_image_digest
    }
  }

  env_vars = local.production_env_vars

  lifecycle {
    ignore_changes = [runtime_source.image.digest]
  }
}

# A second service for checking a release, or demonstrating a faulty one and
# a rollback, without touching production or any real account. Off by
# default; the product release workflow deploys to it when target is set to disposable.
resource "render_web_service" "disposable" {
  count = var.disposable_enabled ? 1 : 0

  name              = "${var.service_name}-disposable"
  plan              = "free"
  region            = var.region
  health_check_path = "/healthz"

  runtime_source = {
    image = {
      image_url = var.image_repo
      digest    = var.bootstrap_image_digest
    }
  }

  env_vars = local.disposable_env_vars

  lifecycle {
    ignore_changes = [runtime_source.image.digest]
  }
}

# Gives production (only) the credentials and the calendar token file in the
# hand-made environment group. This resource's state holds only IDs.
resource "render_env_group_link" "secrets" {
  env_group_id = var.secrets_env_group_id
  service_ids  = [render_web_service.product.id]
}
