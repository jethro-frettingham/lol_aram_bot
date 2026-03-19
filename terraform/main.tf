##############################################################################
# ARAM Mayhem Discord Bot – Terraform Infrastructure
# Cost profile: ~$0–2/month (mostly free tier)
#   - Lambda: free tier (1M invocations/month)
#   - EventBridge: free (< 1M events/month)
#   - SSM Parameter Store: free (standard parameters)
#   - DynamoDB: free tier (25 GB, 25 RCU/WCU on-demand)
#   - CloudWatch Logs: free tier (5 GB ingestion)
##############################################################################

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

locals {
  name_prefix = "aram-bot"
}

##############################################################################
# Lambda package
##############################################################################

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/src"
  output_path = "${path.module}/../lambda/lambda.zip"
}

##############################################################################
# IAM – Lambda execution role
##############################################################################

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name_prefix}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda_perms" {
  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }

  # SSM – read secrets
  statement {
    actions   = ["ssm:GetParameter"]
    resources = [
      aws_ssm_parameter.riot_api_key.arn,
      aws_ssm_parameter.discord_webhook.arn,
      aws_ssm_parameter.anthropic_api_key.arn,
    ]
  }

  # DynamoDB – seen-games table
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
    ]
    resources = [aws_dynamodb_table.seen_games.arn]
  }
}

resource "aws_iam_role_policy" "lambda_perms" {
  name   = "${local.name_prefix}-perms"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_perms.json
}

##############################################################################
# CloudWatch Log Group  (explicit so we can control retention)
##############################################################################

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name_prefix}"
  retention_in_days = 7   # Keep costs low; 7 days is plenty for debugging
}

##############################################################################
# Lambda function
##############################################################################

resource "aws_lambda_function" "bot" {
  function_name    = local.name_prefix
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "index.handler"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  timeout          = 120   # 2 min – Claude + Riot calls can take a moment
  memory_size      = 256   # Plenty for pure Python + HTTP calls

  environment {
    variables = {
      REGION                = var.lol_region
      TRACKED_PLAYERS       = var.tracked_players
      SEEN_GAMES_TABLE      = aws_dynamodb_table.seen_games.name
      RIOT_KEY_PARAM        = aws_ssm_parameter.riot_api_key.name
      DISCORD_WEBHOOK_PARAM = aws_ssm_parameter.discord_webhook.name
      ANTHROPIC_KEY_PARAM   = aws_ssm_parameter.anthropic_api_key.name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy.lambda_perms,
  ]
}

##############################################################################
# EventBridge rule – poll on a schedule
##############################################################################

resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${local.name_prefix}-schedule"
  description         = "Trigger ARAM bot every ${var.poll_interval_minutes} minutes"
  schedule_expression = "rate(${var.poll_interval_minutes} minutes)"
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "${local.name_prefix}-target"
  arn       = aws_lambda_function.bot.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bot.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}

##############################################################################
# DynamoDB – seen-games deduplication table
# On-demand billing = pay only when you play. At friend-group scale: ~$0/month.
##############################################################################

resource "aws_dynamodb_table" "seen_games" {
  name         = "${local.name_prefix}-seen-games"
  billing_mode = "PAY_PER_REQUEST"   # No provisioned capacity = no base cost
  hash_key     = "match_id"

  attribute {
    name = "match_id"
    type = "S"
  }

  # TTL: auto-delete seen games after 30 days (keep table tiny)
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Project = local.name_prefix
  }
}

##############################################################################
# SSM Parameter Store – secrets (SecureString = free, encrypted with KMS default key)
# Values are placeholders; set real values after first apply (see README).
##############################################################################

resource "aws_ssm_parameter" "riot_api_key" {
  name        = "/${local.name_prefix}/riot-api-key"
  description = "Riot Games API key"
  type        = "SecureString"
  value       = var.riot_api_key
}

resource "aws_ssm_parameter" "discord_webhook" {
  name        = "/${local.name_prefix}/discord-webhook-url"
  description = "Discord channel webhook URL"
  type        = "SecureString"
  value       = var.discord_webhook_url
}

resource "aws_ssm_parameter" "anthropic_api_key" {
  name        = "/${local.name_prefix}/anthropic-api-key"
  description = "Anthropic Claude API key"
  type        = "SecureString"
  value       = var.anthropic_api_key
}
