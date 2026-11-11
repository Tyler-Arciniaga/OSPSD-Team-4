# Settings that rarely change. What the services run from (image repository,
# first digest, disposable toggle, secrets group) is in release.tf.

variable "issue_provider" {
  description = "Issue tracker the service uses: trello or linear."
  type        = string
  default     = "trello"

  validation {
    condition     = contains(["trello", "linear"], var.issue_provider)
    error_message = "issue_provider must be \"trello\" or \"linear\"."
  }
}

variable "service_name" {
  description = "Render service name. It also forms the default onrender.com URL."
  type        = string
  default     = "ospsd-team4-issue-tracker"
}

variable "region" {
  description = "Render region. Virginia (US East) is closest to the team in NYC."
  type        = string
  default     = "virginia"
}
