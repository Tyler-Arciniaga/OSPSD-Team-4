# What the services run from. Which image digest is deployed is decided by
# the product release workflow, not here. None of these values are secret.

variable "image_repo" {
  description = "Registry repository the product release workflow pushes the image to. Lowercase, with no tag or digest."
  type        = string
  default     = "ghcr.io/tyler-arciniaga/ospsd-team4-product"

  validation {
    condition     = can(regex("^[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)+$", var.image_repo))
    error_message = "image_repo must be a lowercase registry path with no tag or digest."
  }
}

variable "bootstrap_image_digest" {
  description = <<-EOT
    Image digest (sha256:...) a service starts with when Terraform creates
    it. Ignored after that: releases change the digest. The apply workflow
    passes the latest successful product-production release's digest, or the
    one given by hand for the very first apply. Pull request plans leave it
    unset.
  EOT
  type        = string
  default     = null

  validation {
    condition     = var.bootstrap_image_digest == null || can(regex("^sha256:[0-9a-f]{64}$", var.bootstrap_image_digest))
    error_message = "bootstrap_image_digest must be sha256: followed by 64 lowercase hex characters."
  }
}

variable "disposable_enabled" {
  description = "Create the disposable service, which runs the in-memory providers. Turn it off again afterwards."
  type        = bool
  default     = false
}

variable "secrets_env_group_id" {
  description = <<-EOT
    ID of the Render Environment Group, created by hand, that holds the
    product's provider credentials and the calendar token secret file
    (local.secret_names in main.tf). Only the ID is here: the values never
    enter Terraform or its state. The workflows read it from the repository
    variable PRODUCT_SECRETS_ENV_GROUP_ID.
  EOT
  type        = string

  validation {
    condition     = can(regex("^evg-[a-z0-9]+$", var.secrets_env_group_id))
    error_message = "secrets_env_group_id must be a Render environment group ID (evg-...)."
  }
}
