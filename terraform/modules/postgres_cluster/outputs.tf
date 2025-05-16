output "cluster_endpoint" {
  description = "Writer endpoint for the cluster"
  value       = module.aurora.cluster_endpoint
}

output "cluster_reader_endpoint" {
  description = "A read-only endpoint for the cluster"
  value       = module.aurora.cluster_reader_endpoint
}

output "cluster_id" {
  description = "The ID of the cluster"
  value       = module.aurora.cluster_id
}

output "master_password" {
  description = "The master password for the database"
  value       = random_password.master_password.result
  sensitive   = true
}