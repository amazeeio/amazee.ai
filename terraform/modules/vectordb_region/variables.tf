variable "vpc_name" {
  description = "Name of the VPC"
  type        = string
  default     = "amazeeai-vectordb-vpc"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.10.0.0/16"
}

variable "clusters" {
  description = "Map of RDS cluster configurations"
  type = map(object({
    instance_count      = number
    min_capacity        = number
    max_capacity        = number
    backup_window       = string
    maintenance_window  = string
  }))
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
}

# Default values for instance configuration
locals {
  default_instance_config = {
    instance_class      = "db.serverless"
    publicly_accessible = true
  }
}

variable "instances" {
  description = "Map of instances configuration"
  type = map(object({
    instance_class      = string
    publicly_accessible = bool
  }))
  default = {
    instance1 = {
      instance_class      = "db.serverless"
      publicly_accessible = true
    }
  }
}

variable "min_capacity" {
  description = "Minimum capacity for serverless v2 scaling"
  type        = number
  default     = 2
}

variable "max_capacity" {
  description = "Maximum capacity for serverless v2 scaling"
  type        = number
  default     = 16
}

variable "backup_window" {
  description = "Preferred backup window"
  type        = string
  default     = "06:42-07:12"
}

variable "maintenance_window" {
  description = "Preferred maintenance window"
  type        = string
  default     = "wed:04:35-wed:05:05"
}