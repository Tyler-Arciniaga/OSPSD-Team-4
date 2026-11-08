terraform {
  # Pinned so every teammate and CI produce the same plan. Update deliberately.
  required_version = "~> 1.17.1"

  required_providers {
    render = {
      source  = "render-oss/render"
      version = "1.9.1"
    }
  }

  # State lives in HCP Terraform, shared by the team and locked during runs.
  # The workspace uses Local execution: plan and apply run on the caller's
  # machine or CI runner, which holds the Render API key; HCP only stores and
  # locks the state. Log in once with `terraform login`.
  cloud {
    organization = "ospsd-team-4"

    workspaces {
      name = "ospsd-team4-render"
    }
  }
}
