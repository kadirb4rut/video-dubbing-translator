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
variable "github_actions_ecs_deploy" {
  description = "Grant the GitHub OIDC role least-privilege ECS permissions for the manual CPU acceptance workflow. Keep false unless that workflow is explicitly being run."
  type        = bool
  default     = false
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
  description = "Primary translation adapter. Google/deep-translator is the fast default; AWS Translate, Hy-MT2, and configured-api remain explicit alternatives."
  type        = string
  default     = "google-deep-translator"
  validation {
    condition     = contains(["google-deep-translator", "google", "deep-translator", "hymt2", "configured-api", "aws-translate"], var.translation_provider)
    error_message = "translation_provider must be google-deep-translator, hymt2, configured-api, or aws-translate."
  }
}
variable "translation_refinement_provider" {
  description = "Optional duration-aware linguistic refinement adapter. It is loaded only when the translated TTS exceeds the configured tolerance."
  type        = string
  default     = "hymt2"
  validation {
    condition     = contains(["none", "disabled", "hymt2"], var.translation_refinement_provider)
    error_message = "translation_refinement_provider must be none, disabled, or hymt2."
  }
}
variable "translation_refinement_max_passes" {
  description = "Maximum duration-refinement passes per segment. Keep at one for bounded dubbing retries."
  type        = number
  default     = 1
  validation {
    condition     = var.translation_refinement_max_passes >= 0 && var.translation_refinement_max_passes <= 1
    error_message = "translation_refinement_max_passes must be 0 or 1."
  }
}
variable "translation_model" {
  description = "Self-hosted translation checkpoint used when translation_provider is hymt2."
  type        = string
  default     = "tencent/Hy-MT2-1.8B"
}
variable "translation_model_revision" {
  description = "Immutable Hugging Face revision for the self-hosted translation checkpoint."
  type        = string
  default     = "9a341cd1b679d3efd23b46e847b01745a71ed792"
}
variable "voxcpm_model" {
  description = "VoxCPM2 voice-cloning model identifier."
  type        = string
  default     = "openbmb/VoxCPM2"
}
variable "voxcpm_model_revision" {
  description = "Immutable Hugging Face revision for the VoxCPM2 checkpoint."
  type        = string
  default     = "32279effe8c19989596f05d353d1447f51d9e915"
}
variable "voxcpm_gpu_dtype" {
  description = "VoxCPM2 GPU dtype; FP16 is the safe default for the planned NVIDIA T4."
  type        = string
  default     = "float16"
  validation {
    condition     = contains(["float16", "bfloat16", "float32"], var.voxcpm_gpu_dtype)
    error_message = "voxcpm_gpu_dtype must be float16, bfloat16, or float32."
  }
}
variable "voxcpm_cpu_dtype" {
  description = "VoxCPM2 CPU dtype used by the CPU validation worker."
  type        = string
  default     = "bfloat16"
  validation {
    condition     = contains(["float16", "bfloat16", "float32"], var.voxcpm_cpu_dtype)
    error_message = "voxcpm_cpu_dtype must be float16, bfloat16, or float32."
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
variable "allow_unmeasured_pricing" {
  description = "Temporary validation override. Keep false for production traffic; true only for an explicitly measured acceptance run before cost profiles are published."
  type        = bool
  default     = false
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
variable "cpu_worker_image" {
  description = "Optional CPU worker image used for temporary or low-cost validation. Leave empty to keep the CPU service absent."
  type        = string
  default     = ""
}
variable "cpu_worker_desired_count" {
  description = "CPU validation worker desired count; keep zero when no CPU validation is running."
  type        = number
  default     = 0
  validation {
    condition     = var.cpu_worker_desired_count >= 0
    error_message = "cpu_worker_desired_count must be zero or greater."
  }
}
variable "cpu_worker_cpu" {
  description = "Fargate CPU units for the optional CPU validation worker."
  type        = number
  default     = 4096
}
variable "cpu_worker_memory" {
  description = "Fargate memory in MiB for the optional CPU validation worker."
  type        = number
  default     = 16384
}
variable "cpu_worker_hourly_price_usd" {
  description = "Approximate Fargate CPU plus memory hourly price used for CPU validation telemetry."
  type        = number
  default     = 0.233
  validation {
    condition     = var.cpu_worker_hourly_price_usd >= 0
    error_message = "cpu_worker_hourly_price_usd must be zero or greater."
  }
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
