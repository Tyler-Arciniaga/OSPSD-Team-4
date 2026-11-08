# What is deployed. A deploy request is a pull request that changes this file:
# its plan shows the change, and after merge the manual apply workflow
# deploys exactly this commit. None of these values are secret.

variable "commit_sha" {
  description = "Full 40-character Git commit Render builds and runs. It must be pushed and on var.branch."
  type        = string
  default     = "9ab10b17a387e535dc79384fc22c56a9ab16d7f1"

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.commit_sha))
    error_message = "commit_sha must be a full, lowercase 40-character commit SHA."
  }
}

variable "branch" {
  description = "Branch Render builds from. Switch to main once this work is merged."
  type        = string
  default     = "feature/enable-remote-deployment-via-IaC"
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
