terraform {
  # Same versions as the HW2 configuration (infra/versions.tf).
  required_version = "~> 1.17.1"

  required_providers {
    render = {
      source  = "render-oss/render"
      version = "1.9.1"
    }
  }

  # The product's own state, in its own HCP Terraform workspace, so nothing
  # here can touch the HW2 service's state. Local execution, as for HW2: plan
  # and apply run on the caller's machine or CI runner, and HCP only stores
  # and locks the state. Log in once with `terraform login`.
  cloud {
    organization = "ospsd-team-4"

    workspaces {
      name = "ospsd-team4-product-render"
    }
  }
}
