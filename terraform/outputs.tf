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

# VectorDB EU Central 1 (Frankfurt) Outputs
output "vectordb_eu_central_1_cluster_endpoints" {
  description = "Writer endpoints for each cluster in EU Central 1 (Frankfurt)"
  value       = try(module.vectordb_prod_eu_central_1[0].cluster_endpoints, {})
}

output "vectordb_eu_central_1_cluster_reader_endpoints" {
  description = "Read-only endpoints for each cluster in EU Central 1 (Frankfurt)"
  value       = try(module.vectordb_prod_eu_central_1[0].cluster_reader_endpoints, {})
}

output "vectordb_eu_central_1_cluster_ids" {
  description = "The IDs of each cluster in EU Central 1 (Frankfurt)"
  value       = try(module.vectordb_prod_eu_central_1[0].cluster_ids, {})
}

output "vectordb_eu_central_1_master_passwords" {
  description = "The master passwords for each database in EU Central 1 (Frankfurt)"
  value       = try(module.vectordb_prod_eu_central_1[0].master_passwords, {})
  sensitive   = true
}

# VectorDB EU Central 2 (Zurich) Outputs
output "vectordb_eu_central_2_cluster_endpoints" {
  description = "Writer endpoints for each cluster in EU Central 2 (Zurich)"
  value       = try(module.vectordb_prod_eu_central_2[0].cluster_endpoints, {})
}

output "vectordb_eu_central_2_cluster_reader_endpoints" {
  description = "Read-only endpoints for each cluster in EU Central 2 (Zurich)"
  value       = try(module.vectordb_prod_eu_central_2[0].cluster_reader_endpoints, {})
}

output "vectordb_eu_central_2_cluster_ids" {
  description = "The IDs of each cluster in EU Central 2 (Zurich)"
  value       = try(module.vectordb_prod_eu_central_2[0].cluster_ids, {})
}

output "vectordb_eu_central_2_master_passwords" {
  description = "The master passwords for each database in EU Central 2 (Zurich)"
  value       = try(module.vectordb_prod_eu_central_2[0].master_passwords, {})
  sensitive   = true
}

# VectorDB US East 1 (N. Virginia) Outputs
output "vectordb_us_east_1_cluster_endpoints" {
  description = "Writer endpoints for each cluster in US East 1 (N. Virginia)"
  value       = try(module.vectordb_prod_us_east_1[0].cluster_endpoints, {})
}

output "vectordb_us_east_1_cluster_reader_endpoints" {
  description = "Read-only endpoints for each cluster in US East 1 (N. Virginia)"
  value       = try(module.vectordb_prod_us_east_1[0].cluster_reader_endpoints, {})
}

output "vectordb_us_east_1_cluster_ids" {
  description = "The IDs of each cluster in US East 1 (N. Virginia)"
  value       = try(module.vectordb_prod_us_east_1[0].cluster_ids, {})
}

output "vectordb_us_east_1_master_passwords" {
  description = "The master passwords for each database in US East 1 (N. Virginia)"
  value       = try(module.vectordb_prod_us_east_1[0].master_passwords, {})
  sensitive   = true
}

# VectorDB EU West 2 (London) Outputs
output "vectordb_eu_west_2_cluster_endpoints" {
  description = "Writer endpoints for each cluster in EU West 2 (London)"
  value       = try(module.vectordb_prod_eu_west_2[0].cluster_endpoints, {})
}

output "vectordb_eu_west_2_cluster_reader_endpoints" {
  description = "Read-only endpoints for each cluster in EU West 2 (London)"
  value       = try(module.vectordb_prod_eu_west_2[0].cluster_reader_endpoints, {})
}

output "vectordb_eu_west_2_cluster_ids" {
  description = "The IDs of each cluster in EU West 2 (London)"
  value       = try(module.vectordb_prod_eu_west_2[0].cluster_ids, {})
}

output "vectordb_eu_west_2_master_passwords" {
  description = "The master passwords for each database in EU West 2 (London)"
  value       = try(module.vectordb_prod_eu_west_2[0].master_passwords, {})
  sensitive   = true
}

# VectorDB AP Southeast 2 (Sydney) Outputs
output "vectordb_ap_southeast_2_cluster_endpoints" {
  description = "Writer endpoints for each cluster in AP Southeast 2 (Sydney)"
  value       = try(module.vectordb_prod_ap_southeast_2[0].cluster_endpoints, {})
}

output "vectordb_ap_southeast_2_cluster_reader_endpoints" {
  description = "Read-only endpoints for each cluster in AP Southeast 2 (Sydney)"
  value       = try(module.vectordb_prod_ap_southeast_2[0].cluster_reader_endpoints, {})
}

output "vectordb_ap_southeast_2_cluster_ids" {
  description = "The IDs of each cluster in AP Southeast 2 (Sydney)"
  value       = try(module.vectordb_prod_ap_southeast_2[0].cluster_ids, {})
}

output "vectordb_ap_southeast_2_master_passwords" {
  description = "The master passwords for each database in AP Southeast 2 (Sydney)"
  value       = try(module.vectordb_prod_ap_southeast_2[0].master_passwords, {})
  sensitive   = true
}

# VectorDB CA Central 1 (Canada) Outputs
output "vectordb_ca_central_1_cluster_endpoints" {
  description = "Writer endpoints for each cluster in CA Central 1 (Canada)"
  value       = try(module.vectordb_prod_ca_central_1[0].cluster_endpoints, {})
}

output "vectordb_ca_central_1_cluster_reader_endpoints" {
  description = "Read-only endpoints for each cluster in CA Central 1 (Canada)"
  value       = try(module.vectordb_prod_ca_central_1[0].cluster_reader_endpoints, {})
}

output "vectordb_ca_central_1_cluster_ids" {
  description = "The IDs of each cluster in CA Central 1 (Canada)"
  value       = try(module.vectordb_prod_ca_central_1[0].cluster_ids, {})
}

output "vectordb_ca_central_1_master_passwords" {
  description = "The master passwords for each database in CA Central 1 (Canada)"
  value       = try(module.vectordb_prod_ca_central_1[0].master_passwords, {})
  sensitive   = true
}