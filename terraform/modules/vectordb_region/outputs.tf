output "cluster_endpoints" {
  description = "Writer endpoints for each cluster"
  value       = { for k, v in module.postgres_cluster : k => v.cluster_endpoint }
}

output "cluster_reader_endpoints" {
  description = "Read-only endpoints for each cluster"
  value       = { for k, v in module.postgres_cluster : k => v.cluster_reader_endpoint }
}

output "cluster_ids" {
  description = "The IDs of each cluster"
  value       = { for k, v in module.postgres_cluster : k => v.cluster_id }
}

output "master_passwords" {
  description = "The master passwords for each database"
  value       = { for k, v in module.postgres_cluster : k => v.master_password }
  sensitive   = true
}
