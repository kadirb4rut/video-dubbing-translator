variable "aws_region" {
  type    = string
  default = "eu-north-1"
}
variable "name" {
  type    = string
  default = "lingowave"
}
variable "github_repository" {
  description = "GitHub owner/repository allowed to assume the image-publish role through OIDC."
  type        = string
  default     = "kadirb4rut/video-dubbing-translator"
}
variable "create_network" {
  description = "Create a minimal VPC, public worker subnets, database subnets, and security groups. Set false to use existing network IDs."
  type        = bool
  default     = true
}
variable "network_cidr" {
  type    = string
  default = "10.42.0.0/16"
}
variable "vpc_id" {
  type    = string
  default = ""
}
variable "worker_subnet_ids" {
  type    = list(string)
  default = []
}
variable "worker_security_group_id" {
  type    = string
  default = ""
}
variable "worker_image" { type = string }
variable "api_image" {
  description = "Container image for the FastAPI service. Leave empty to provision worker/storage infrastructure only."
  type        = string
  default     = ""
}
variable "worker_secrets" {
  description = "Map of ECS environment variable names to Secrets Manager secret or secret-version ARNs. Typical entries include DATABASE_URL and TRANSLATION_API_KEY."
  type        = map(string)
  default     = {}
}
variable "translation_provider" {
  description = "Translation adapter for production workers. Use aws-translate to use the task role instead of an external API key."
  type        = string
  default     = "aws-translate"
  validation {
    condition     = contains(["configured-api", "aws-translate"], var.translation_provider)
    error_message = "translation_provider must be configured-api or aws-translate."
  }
}
variable "api_secrets" {
  description = "Map of ECS API environment variable names to Secrets Manager secret or secret-version ARNs. DATABASE_URL is required when api_image is set."
  type        = map(string)
  default     = {}
}
variable "mail_provider" {
  description = "API mail transport. Use ses with a verified sender and task-role permission, smtp for an external relay, or dev only for local SQLite."
  type        = string
  default     = "dev"
  validation {
    condition     = contains(["dev", "smtp", "ses"], var.mail_provider)
    error_message = "mail_provider must be dev, smtp, or ses."
  }
}
variable "mail_from" {
  description = "Verified sender address used by SMTP or SES."
  type        = string
  default     = "no-reply@lingowave.local"
}
variable "ses_identity_arn" {
  description = "Verified SES identity ARN. Required when mail_provider is ses so SendEmail can be scoped to one identity."
  type        = string
  default     = ""
}
variable "api_subnet_ids" {
  description = "Subnets for the API service and public load balancer when create_network is false."
  type        = list(string)
  default     = []
}
variable "api_security_group_id" {
  description = "Security group for API tasks when create_network is false."
  type        = string
  default     = ""
}
variable "load_balancer_security_group_id" {
  description = "Security group for the public API load balancer when create_network is false."
  type        = string
  default     = ""
}
variable "api_desired_count" {
  description = "Steady-state API task count. The API is separate from scale-to-zero GPU workers."
  type        = number
  default     = 1
  validation {
    condition     = var.api_desired_count >= 0
    error_message = "api_desired_count must be zero or greater."
  }
}
variable "api_certificate_arn" {
  description = "Optional ACM certificate ARN for a direct HTTPS API listener. Leave empty when the shared CloudFront distribution terminates HTTPS."
  type        = string
  default     = ""
}
variable "frontend_enabled" {
  description = "Provision the private frontend bucket and shared CloudFront HTTPS distribution."
  type        = bool
  default     = false
}
variable "frontend_dist_dir" {
  description = "Built frontend directory to upload when frontend_enabled is true."
  type        = string
  default     = ""
}
variable "frontend_origin" {
  type    = string
  default = "http://localhost:5173"
}
variable "media_retention_days" {
  type    = number
  default = 30
}
variable "sqs_visibility_timeout_seconds" {
  type    = number
  default = 3600
  validation {
    condition     = var.sqs_visibility_timeout_seconds >= 60 && var.sqs_visibility_timeout_seconds <= 43200
    error_message = "sqs_visibility_timeout_seconds must be between 60 seconds and 12 hours."
  }
}
variable "worker_instance_type" {
  type    = string
  default = "g4dn.xlarge"
}
variable "worker_hourly_price_usd" {
  description = "On-demand hourly price used for approximate worker cost telemetry. Keep aligned with the selected instance type and region."
  type        = number
  default     = 0.558
  validation {
    condition     = var.worker_hourly_price_usd >= 0
    error_message = "worker_hourly_price_usd must be zero or greater."
  }
}
variable "worker_desired_count" {
  type    = number
  default = 0
}
variable "enable_rds" {
  type    = bool
  default = false
}
variable "database_subnet_ids" {
  type    = list(string)
  default = []
}
variable "database_security_group_id" {
  type    = string
  default = ""
}
variable "database_name" {
  type    = string
  default = "lingowave"
}
variable "database_username" {
  type    = string
  default = "lingowave"
}
variable "database_password" {
  type      = string
  sensitive = true
}
