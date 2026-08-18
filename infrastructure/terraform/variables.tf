variable "aws_region" {
  type    = string
  default = "eu-central-1"
}
variable "name" {
  type    = string
  default = "lingowave"
}
variable "vpc_id" { type = string }
variable "worker_subnet_ids" { type = list(string) }
variable "worker_security_group_id" { type = string }
variable "worker_image" { type = string }
variable "worker_instance_type" {
  type    = string
  default = "g4dn.xlarge"
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
  default   = "change-me-before-apply"
}
