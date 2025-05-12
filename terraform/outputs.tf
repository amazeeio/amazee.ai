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

output "role_assumer_access_key_id" {
  description = "Access key ID for the role assumer user"
  value       = aws_iam_access_key.role_assumer.id
}

output "role_assumer_secret_key" {
  description = "Secret access key for the role assumer user"
  value       = aws_iam_access_key.role_assumer.secret
  sensitive   = true
}

output "litellm_access_key_id" {
  description = "The access key ID for the LiteLLM IAM user"
  value       = aws_iam_access_key.litellm.id
  sensitive   = false
}

output "litellm_access_key_secret" {
  description = "The access key secret for the LiteLLM IAM user"
  value       = aws_iam_access_key.litellm.secret
  sensitive   = true
}