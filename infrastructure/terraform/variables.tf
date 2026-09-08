variable "aws_region" {
  type    = string
  default = "eu-north-1"
}
variable "name" {
  type    = string
  default = "lingowave"
}
variable "deployment_mode" {
  description = "Use legacy ECS/RDS resources for rollback or the serverless control plane with Aurora Serverless v2."
  type        = string
  default     = "legacy"
  validation {
    condition     = contains(["legacy", "serverless"], var.deployment_mode)
    error_message = "deployment_mode must be legacy or serverless."
  }
}
variable "serverless_frontend_cutover" {
  description = "Route the shared CloudFront API paths to API Gateway. Keep false while Aurora data migration and direct serverless validation are in progress; enable only in a separately reviewed cutover apply."
  type        = bool
  default     = false
}
variable "github_repository" {
  description = "GitHub owner/repository allowed to assume the image-publish role through OIDC."
  type        = string
  default     = "kadirb4rut/video-dubbing-translator"
}
variable "github_actions_branch" {
  description = "Single production branch allowed to assume the image-publish OIDC role. Pull requests and other branches are intentionally excluded."
  type        = string
  default     = "codex/production-saas"
  validation {
    condition     = length(trimspace(var.github_actions_branch)) > 0 && !strcontains(var.github_actions_branch, "refs/")
    error_message = "github_actions_branch must be a non-empty branch name without a refs/ prefix."
  }
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
variable "retain_legacy_api" {
  description = "Migration guard: keep the existing ECS API and ALB while API Gateway/Lambda is validated. Requires api_image and legacy api_secrets during the migration window."
  type        = bool
  default     = false
}
variable "lambda_api_image" {
  description = "Immutable Lambda-compatible API image URI/digest used when deployment_mode is serverless."
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
variable "voxcpm_allow_download" {
  description = "Allow workers to download the pinned VoxCPM2 checkpoint on first use. Keep false when the image or host cache is prewarmed."
  type        = bool
  default     = false
}
variable "api_secrets" {
  description = "Map of ECS API environment variable names to Secrets Manager secret or secret-version ARNs. ECS JSON-key selectors are supported (for example, SECRET_ARN:JSON_KEY::). DATABASE_URL is required when api_image is set; Google OAuth uses GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI when enabled."
  type        = map(string)
  default     = {}
}
variable "ssm_parameter_map" {
  description = "Map of application environment variable names to SSM Parameter Store Standard parameter names. Values are loaded by Lambda at cold start."
  type        = map(string)
  default     = {}
}
variable "ssm_parameter_arns" {
  description = "SSM parameter ARNs allowed for the serverless Lambda role. Keep this list explicit for least privilege."
  type        = list(string)
  default     = []
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
variable "gpu_market_type" {
  description = "GPU EC2 market for the optional ECS GPU fleet. Keep on-demand until Spot interruption recovery has been accepted in the target region."
  type        = string
  default     = "on-demand"
  validation {
    condition     = contains(["on-demand", "spot"], var.gpu_market_type)
    error_message = "gpu_market_type must be on-demand or spot."
  }
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
variable "worker_compute_mode" {
  description = "Queue-backed worker fleet to autoscale. Use cpu while the GPU quota is pending, then switch to gpu after measured GPU capacity is available. Only one fleet consumes the shared queue at a time."
  type        = string
  default     = "gpu"
  validation {
    condition     = contains(["disabled", "cpu", "gpu"], var.worker_compute_mode)
    error_message = "worker_compute_mode must be disabled, cpu, or gpu."
  }
}
variable "worker_max_count" {
  description = "Maximum number of concurrent queue workers for the active compute fleet. Each worker claims one job at a time."
  type        = number
  default     = 10
  validation {
    condition     = var.worker_max_count >= 1 && var.worker_max_count <= 100
    error_message = "worker_max_count must be between 1 and 100."
  }
}
variable "cpu_worker_max_count" {
  description = "Maximum number of concurrent Fargate Spot CPU workers. Keep this at or below the verified regional vCPU quota; one 4096-CPU task is the safe default for the current account."
  type        = number
  default     = 1
  validation {
    condition     = var.cpu_worker_max_count >= 1 && var.cpu_worker_max_count <= 100
    error_message = "cpu_worker_max_count must be between 1 and 100."
  }
}
variable "worker_scale_out_cooldown_seconds" {
  description = "Cooldown between queue-driven scale-out actions."
  type        = number
  default     = 60
  validation {
    condition     = var.worker_scale_out_cooldown_seconds >= 0
    error_message = "worker_scale_out_cooldown_seconds must be zero or greater."
  }
}
variable "worker_scale_in_cooldown_seconds" {
  description = "Cooldown after the active queue has drained before scaling workers toward zero."
  type        = number
  default     = 300
  validation {
    condition     = var.worker_scale_in_cooldown_seconds >= 60
    error_message = "worker_scale_in_cooldown_seconds must be at least 60 seconds."
  }
}
variable "worker_scale_in_evaluation_periods" {
  description = "Consecutive one-minute empty-queue periods required before scale-in."
  type        = number
  default     = 5
  validation {
    condition     = var.worker_scale_in_evaluation_periods >= 1 && var.worker_scale_in_evaluation_periods <= 60
    error_message = "worker_scale_in_evaluation_periods must be between 1 and 60."
  }
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
variable "cpu_worker_ephemeral_storage_gib" {
  description = "Fargate ephemeral storage for the CPU worker image and model cache."
  type        = number
  default     = 50
  validation {
    condition     = var.cpu_worker_ephemeral_storage_gib >= 20 && var.cpu_worker_ephemeral_storage_gib <= 200
    error_message = "cpu_worker_ephemeral_storage_gib must be between 20 and 200 GiB."
  }
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
variable "retain_legacy_rds" {
  description = "Migration guard: keep the existing provisioned PostgreSQL instance while Aurora Serverless v2 is validated. Set true only for an existing RDS-backed state, then set false in a later cleanup apply."
  type        = bool
  default     = false
}
variable "enable_aurora_serverless" {
  description = "Provision Aurora Serverless v2 with 0 ACU auto-pause for the serverless deployment mode."
  type        = bool
  default     = false
}
variable "aurora_provisioning_mode" {
  description = "Use terraform-vpc on a paid AWS plan, or express-existing for an AWS Free plan Aurora Express cluster created with the official bootstrap flow."
  type        = string
  default     = "terraform-vpc"
  validation {
    condition     = contains(["terraform-vpc", "express-existing"], var.aurora_provisioning_mode)
    error_message = "aurora_provisioning_mode must be terraform-vpc or express-existing."
  }
}
variable "aurora_external_cluster_arn" {
  description = "Existing Aurora Express cluster ARN used when aurora_provisioning_mode=express-existing."
  type        = string
  default     = ""
}
variable "aurora_external_secret_arn" {
  description = "Secrets Manager ARN containing the non-master Aurora Express application credentials."
  type        = string
  default     = ""
}
variable "aurora_engine_version" {
  description = "Aurora PostgreSQL engine version verified for the selected region before apply."
  type        = string
  default     = "16.8"
}
variable "aurora_max_acu" {
  description = "Maximum Aurora Serverless v2 capacity for the control-plane database."
  type        = number
  default     = 1
  validation {
    condition     = var.aurora_max_acu >= 0.5
    error_message = "aurora_max_acu must be at least 0.5 ACU."
  }
}
variable "aurora_auto_pause_seconds" {
  description = "Idle seconds before Aurora Serverless v2 attempts to pause; AWS minimum is five minutes."
  type        = number
  default     = 300
  validation {
    condition     = var.aurora_auto_pause_seconds >= 300 && var.aurora_auto_pause_seconds <= 86400
    error_message = "aurora_auto_pause_seconds must be between 300 and 86400 seconds."
  }
}
variable "cost_alert_email" {
  description = "Optional email for AWS Budget and Cost Anomaly Detection notifications. Leave empty to avoid creating email subscriptions."
  type        = string
  default     = ""
}
variable "monthly_cost_budget_usd" {
  description = "Monthly AWS budget for baseline spend alerts. Actual user-driven compute is still metered separately."
  type        = number
  default     = 5
  validation {
    condition     = var.monthly_cost_budget_usd > 0
    error_message = "monthly_cost_budget_usd must be positive."
  }
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
  default   = null
}
variable "legacy_rds_backup_retention_days" {
  description = "Automated backup retention for the legacy PostgreSQL source during rollback or migration."
  type        = number
  default     = 7
  validation {
    condition     = var.legacy_rds_backup_retention_days >= 0 && var.legacy_rds_backup_retention_days <= 35
    error_message = "legacy_rds_backup_retention_days must be between 0 and 35 days."
  }
}
