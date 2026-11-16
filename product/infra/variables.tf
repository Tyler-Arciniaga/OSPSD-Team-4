# Settings that rarely change. What the services run from is in release.tf.

variable "service_name" {
  description = "Render service name. It also forms the default onrender.com URL."
  type        = string
  default     = "ospsd-team4-product"
}

variable "region" {
  description = "Render region. Virginia (US East) is closest to the team in NYC."
  type        = string
  default     = "virginia"
}

variable "calendar_token_file_name" {
  description = <<-EOT
    File name of the Google OAuth token secret file in the environment group.
    Render mounts secret files at /etc/secrets/<name>; CALENDAR_TOKEN_FILE
    points there.
  EOT
  type        = string
  default     = "google-calendar-token.json"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+$", var.calendar_token_file_name))
    error_message = "calendar_token_file_name must be a plain file name."
  }
}

variable "default_timezone" {
  description = "IANA time zone used when a request gives none."
  type        = string
  default     = "America/New_York"
}
