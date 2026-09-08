resource "aws_budgets_budget" "monthly" {
  count        = var.cost_alert_email != "" ? 1 : 0
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_cost_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.cost_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.cost_alert_email]
  }
}

resource "aws_ce_anomaly_monitor" "services" {
  count             = var.cost_alert_email != "" ? 1 : 0
  name              = "${var.name}-services"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "services" {
  count            = var.cost_alert_email != "" ? 1 : 0
  name             = "${var.name}-daily-anomalies"
  frequency        = "DAILY"
  monitor_arn_list = [aws_ce_anomaly_monitor.services[0].arn]
  subscriber {
    type    = "EMAIL"
    address = var.cost_alert_email
  }
}

resource "aws_cloudwatch_log_group" "serverless_api" {
  count             = local.serverless_api_enabled ? 1 : 0
  name              = "/aws/lambda/${var.name}-api"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "serverless_migration" {
  count             = local.serverless_api_enabled && local.serverless_db_enabled ? 1 : 0
  name              = "/aws/lambda/${var.name}-migration"
  retention_in_days = 7
}
