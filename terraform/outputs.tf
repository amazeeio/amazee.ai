output "email_role_arn" {
  description = "ARN of the IAM role for email operations"
  value       = aws_iam_role.amazeeai_send_email.arn
}

output "ddb_role_arn" {
  description = "ARN of the IAM role for DynamoDB operations"
  value       = aws_iam_role.amazeeai_ddb.arn
}

output "role_assumer_user_arn" {
  description = "ARN of the IAM user that can assume roles"
  value       = aws_iam_user.role_assumer.arn
}

output "role_assumer_user_name" {
  description = "Name of the IAM user that can assume roles"
  value       = aws_iam_user.role_assumer.name
}