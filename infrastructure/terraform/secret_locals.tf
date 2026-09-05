locals {
  # ECS supports selecting one JSON member from a Secrets Manager secret with
  # a valueFrom suffix such as `:GOOGLE_CLIENT_ID::`. IAM evaluates
  # GetSecretValue against the base secret ARN, so strip the ECS selector
  # before building the execution-role policy resource list.
  secret_value_froms = concat(values(var.worker_secrets), values(var.api_secrets))
  secret_policy_arns = distinct([
    for value_from in local.secret_value_froms : join(":", slice(split(":", value_from), 0, 7))
  ])
}
