##############################################################################
# Outputs
##############################################################################

output "lambda_function_name" {
  description = "Lambda function name (use to tail logs or trigger manually)"
  value       = aws_lambda_function.bot.function_name
}

output "lambda_function_arn" {
  description = "Lambda function ARN"
  value       = aws_lambda_function.bot.arn
}

output "dynamodb_table_name" {
  description = "DynamoDB seen-games table name"
  value       = aws_dynamodb_table.seen_games.name
}

output "log_group" {
  description = "CloudWatch log group for Lambda logs"
  value       = aws_cloudwatch_log_group.lambda.name
}

output "schedule" {
  description = "EventBridge schedule expression"
  value       = aws_cloudwatch_event_rule.schedule.schedule_expression
}

output "tail_logs_command" {
  description = "Run this to tail Lambda logs in real-time"
  value       = "aws logs tail ${aws_cloudwatch_log_group.lambda.name} --follow --region ${var.aws_region}"
}

output "manual_invoke_command" {
  description = "Run this to manually trigger the bot (useful for testing)"
  value       = "aws lambda invoke --function-name ${aws_lambda_function.bot.function_name} --region ${var.aws_region} /tmp/bot-response.json && cat /tmp/bot-response.json"
}
