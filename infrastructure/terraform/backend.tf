terraform {
  # Environment-specific state settings are supplied with
  # -backend-config=environments/backend-<environment>.hcl.
  backend "s3" {}
}
