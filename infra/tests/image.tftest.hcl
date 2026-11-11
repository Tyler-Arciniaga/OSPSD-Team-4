# How the configuration pins images, checked with a mock Render provider: no
# credentials, network or real services are involved.
#
#   cd infra && terraform init -backend=false && terraform test
#
# Terraform sets a service's digest only when it creates the service; later
# digests come from releases and must not show up as changes. Getting this
# wrong would make every plan after a release try to put an old image back.

mock_provider "render" {}

variables {
  bootstrap_image_digest = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
}

run "creates_production_pinned_to_the_bootstrap_digest" {
  assert {
    condition     = render_web_service.api.runtime_source.image.digest == var.bootstrap_image_digest
    error_message = "The service must start on the bootstrap digest."
  }
  assert {
    condition     = render_web_service.api.runtime_source.image.image_url == "ghcr.io/tyler-arciniaga/ospsd-team4-issue-service"
    error_message = "The service must run images from the release repository."
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
    condition     = render_web_service.api.runtime_source.image.digest == "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    error_message = "A new digest must not replace the one the service already runs."
  }
}

run "disposable_service_starts_on_the_given_digest_and_shares_the_secrets" {
  variables {
    disposable_enabled     = true
    bootstrap_image_digest = "sha256:3333333333333333333333333333333333333333333333333333333333333333"
  }

  assert {
    condition     = render_web_service.disposable[0].runtime_source.image.digest == "sha256:3333333333333333333333333333333333333333333333333333333333333333"
    error_message = "A newly created disposable service must start on the given digest."
  }
  assert {
    condition     = render_web_service.disposable[0].name == "ospsd-team4-issue-tracker-disposable"
    error_message = "The disposable service must have its own name and URL."
  }
  assert {
    condition     = contains(render_env_group_link.secrets.service_ids, render_web_service.disposable[0].id)
    error_message = "The disposable service must get the provider credentials."
  }
  assert {
    condition     = render_web_service.api.runtime_source.image.digest == "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    error_message = "Creating the disposable service must not touch production's digest."
  }
}

run "disposable_service_is_removed_when_turned_off" {
  variables {
    disposable_enabled = false
  }

  assert {
    condition     = length(render_web_service.disposable) == 0
    error_message = "Turning the toggle off must remove the disposable service."
  }
  assert {
    condition     = render_env_group_link.secrets.service_ids == toset([render_web_service.api.id])
    error_message = "Only production keeps the credentials."
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
    image_repo = "ghcr.io/tyler-arciniaga/ospsd-team4-issue-service:latest"
  }
  expect_failures = [var.image_repo]
}
