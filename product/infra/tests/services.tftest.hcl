# How the product configuration pins images and wires providers, checked with
# a mock Render provider: no credentials, network or real services.
#
#   cd product/infra && terraform init -backend=false && terraform test

mock_provider "render" {}

variables {
  bootstrap_image_digest = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
  secrets_env_group_id   = "evg-test0000000000000000"
}

run "production_starts_on_the_bootstrap_digest_with_the_real_providers" {
  assert {
    condition     = render_web_service.product.runtime_source.image.digest == var.bootstrap_image_digest
    error_message = "The service must start on the bootstrap digest."
  }
  assert {
    condition     = render_web_service.product.runtime_source.image.image_url == "ghcr.io/tyler-arciniaga/ospsd-team4-product"
    error_message = "The service must run images from the product repository."
  }
  assert {
    condition     = render_web_service.product.env_vars["CALENDAR_PROVIDER"].value == "team5_calendar:create_calendar_service"
    error_message = "Production must use Team 5's calendar."
  }
  assert {
    condition     = render_web_service.product.env_vars["STORAGE_PROVIDER"].value == "dropbox_client_impl:DropboxDocumentStorage"
    error_message = "Production must use Team 3's storage."
  }
  assert {
    condition     = render_web_service.product.env_vars["TRACKER_PROVIDER"].value == "team_4_issue_tracker_trello:TrelloIssueProvider"
    error_message = "Production must use our tracker package."
  }
  assert {
    condition     = render_web_service.product.env_vars["CALENDAR_TOKEN_FILE"].value == "/etc/secrets/google-calendar-token.json"
    error_message = "CALENDAR_TOKEN_FILE must point at the mounted secret file."
  }
  assert {
    condition     = render_env_group_link.secrets.service_ids == toset([render_web_service.product.id])
    error_message = "Only production gets the credentials."
  }
  assert {
    condition     = !contains(keys(render_web_service.product.env_vars), "STATE_DB_URL")
    error_message = "Production's STATE_DB_URL holds a password: it must come from the env group, never from Terraform."
  }
  assert {
    condition     = contains(output.required_secrets.env_vars, "STATE_DB_URL")
    error_message = "STATE_DB_URL must be listed as a secret set by hand in the env group."
  }
  assert {
    condition     = length(render_web_service.disposable) == 0 && output.disposable_service_id == null
    error_message = "The disposable service must be off by default."
  }
}

run "later_digests_are_left_to_releases" {
  variables {
    bootstrap_image_digest = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
  }
  command = plan

  assert {
    condition     = render_web_service.product.runtime_source.image.digest == "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    error_message = "A new digest must not replace the one the service already runs."
  }
}

run "disposable_service_runs_the_fakes_without_secrets" {
  variables {
    disposable_enabled     = true
    bootstrap_image_digest = "sha256:3333333333333333333333333333333333333333333333333333333333333333"
  }

  assert {
    condition     = render_web_service.disposable[0].name == "ospsd-team4-product-disposable"
    error_message = "The disposable service must have its own name and URL."
  }
  assert {
    condition     = render_web_service.disposable[0].runtime_source.image.digest == "sha256:3333333333333333333333333333333333333333333333333333333333333333"
    error_message = "A newly created disposable service must start on the given digest."
  }
  assert {
    condition = alltrue([
      for name in ["TRACKER_PROVIDER", "CALENDAR_PROVIDER", "STORAGE_PROVIDER"] :
      startswith(render_web_service.disposable[0].env_vars[name].value, "ospsd_team4_product.fakes:")
    ])
    error_message = "The disposable service must select the in-memory providers."
  }
  assert {
    condition     = !contains(keys(render_web_service.disposable[0].env_vars), "CALENDAR_TOKEN_FILE")
    error_message = "The disposable service needs no calendar token."
  }
  assert {
    condition     = render_web_service.disposable[0].env_vars["STATE_DB_URL"].value == "memory://"
    error_message = "The disposable service must use the in-memory state store, never production's database."
  }
  assert {
    condition     = !contains(render_env_group_link.secrets.service_ids, render_web_service.disposable[0].id)
    error_message = "The disposable service must not get the credentials."
  }
  assert {
    condition     = render_web_service.product.runtime_source.image.digest == "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    error_message = "Creating the disposable service must not touch production's digest."
  }
}

run "rejects_a_tag_as_the_digest" {
  command = plan
  variables {
    bootstrap_image_digest = "latest"
  }
  expect_failures = [var.bootstrap_image_digest]
}

run "rejects_an_image_repo_with_a_tag" {
  command = plan
  variables {
    image_repo = "ghcr.io/tyler-arciniaga/ospsd-team4-product:latest"
  }
  expect_failures = [var.image_repo]
}

run "rejects_a_bad_env_group_id" {
  command = plan
  variables {
    secrets_env_group_id = "not-a-group"
  }
  expect_failures = [var.secrets_env_group_id]
}
