# What the services run from. Which image digest is deployed is decided by
# the release workflow (.github/workflows/release.yml), not here: merges to
# main deploy without changing this file. None of these values are secret.

variable "image_repo" {
  description = "Registry repository the release workflow pushes the service image to. Lowercase, with no tag or digest."
  type        = string
  default     = "ghcr.io/tyler-arciniaga/ospsd-team4-issue-service"

  validation {
    condition     = can(regex("^[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)+$", var.image_repo))
    error_message = "image_repo must be a lowercase registry path with no tag or digest."
  }
}

variable "bootstrap_image_digest" {
  description = <<-EOT
    Image digest (sha256:...) a service starts with when Terraform creates it
    or first switches it to the image runtime. Ignored after that: releases
    change the digest. The apply workflow passes the latest successful
    production release's digest, or the one given by hand for the very first
    apply. Pull request plans leave it unset.
  EOT
  type        = string
  default     = null

  validation {
    condition     = var.bootstrap_image_digest == null || can(regex("^sha256:[0-9a-f]{64}$", var.bootstrap_image_digest))
    error_message = "bootstrap_image_digest must be sha256: followed by 64 lowercase hex characters."
  }
}

variable "disposable_enabled" {
  description = "Create the disposable service used to demonstrate a faulty release and a rollback. Turn it off again afterwards."
  type        = bool
  default     = false
}

variable "secrets_env_group_id" {
  description = <<-EOT
    ID of the Render Environment Group, created by hand in the dashboard,
    that holds the provider credentials (local.secret_env_var_names in
    main.tf). Only the ID is here: the values never enter Terraform or its
    state.
  EOT
  type        = string
  default     = "evg-dboft06417fc7384rjfg"

  validation {
    condition     = can(regex("^evg-[a-z0-9]+$", var.secrets_env_group_id))
    error_message = "secrets_env_group_id must be a Render environment group ID (evg-...)."
  }
}
