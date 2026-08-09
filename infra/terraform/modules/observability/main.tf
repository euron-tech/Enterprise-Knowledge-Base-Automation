/** Encrypted log groups, metric filters, alarms. */
terraform {
  required_version = ">= 1.6"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }
}

variable "project" { type = string }
variable "environment" { type = string }
variable "kms_key_arn" { type = string }
variable "retention_days" { type = number }
variable "alarm_topic_arn" { type = string }
variable "tags" { type = map(string) }

locals {
  log_groups = ["api", "ingest-worker"]
  ns         = "EKBA/${var.environment}"
}

resource "aws_cloudwatch_log_group" "this" {
  for_each          = toset(local.log_groups)
  name              = "/${var.project}/${var.environment}/${each.value}"
  retention_in_days = var.retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

# Structured JSON logs -> metrics
resource "aws_cloudwatch_log_metric_filter" "auth_failures" {
  name           = "${var.project}-${var.environment}-auth-failures"
  log_group_name = aws_cloudwatch_log_group.this["api"].name
  pattern        = "{ $.action = \"auth.failure\" }"
  metric_transformation {
    name      = "AuthenticationFailures"
    namespace = local.ns
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "guardrail_blocks" {
  name           = "${var.project}-${var.environment}-guardrail-blocks"
  log_group_name = aws_cloudwatch_log_group.this["api"].name
  pattern        = "{ $.action = \"guardrail.*\" }"
  metric_transformation {
    name      = "GuardrailBlocks"
    namespace = local.ns
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "budget_exceeded" {
  name           = "${var.project}-${var.environment}-agent-budget-exceeded"
  log_group_name = aws_cloudwatch_log_group.this["api"].name
  pattern        = "{ $.message = \"agent.budget_exceeded\" }"
  metric_transformation {
    name      = "AgentBudgetExceeded"
    namespace = local.ns
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "server_errors" {
  name           = "${var.project}-${var.environment}-5xx"
  log_group_name = aws_cloudwatch_log_group.this["api"].name
  pattern        = "{ $.status >= 500 }"
  metric_transformation {
    name      = "HttpServerErrors"
    namespace = local.ns
    value     = "1"
  }
}

locals {
  alarms = {
    high_5xx = {
      metric = "HttpServerErrors", threshold = 10, evaluation = 2, period = 300
      description = "elevated 5xx rate"
    }
    auth_failure_spike = {
      metric = "AuthenticationFailures", threshold = 50, evaluation = 2, period = 300
      description = "authentication failure spike"
    }
    agent_budget_breaches = {
      metric = "AgentBudgetExceeded", threshold = 20, evaluation = 2, period = 900
      description = "agent loop budget breaches"
    }
    guardrail_block_spike = {
      metric = "GuardrailBlocks", threshold = 100, evaluation = 2, period = 900
      description = "guardrail block spike"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each            = local.alarms
  alarm_name          = "${var.project}-${var.environment}-${each.key}"
  alarm_description   = each.value.description
  namespace           = local.ns
  metric_name         = each.value.metric
  statistic           = "Sum"
  period              = each.value.period
  evaluation_periods  = each.value.evaluation
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
  ok_actions          = [var.alarm_topic_arn]
  tags                = var.tags
}

output "log_group_names" { value = { for k, v in aws_cloudwatch_log_group.this : k => v.name } }
output "alarm_arns" { value = [for a in aws_cloudwatch_metric_alarm.this : a.arn] }
