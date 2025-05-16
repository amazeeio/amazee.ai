variable "name" {
  description = "Name of the RDS cluster"
  type        = string
}

variable "availability_zones" {
  description = "List of availability zones"
  type        = list(string)
}

variable "vpc_id" {
  description = "VPC ID where the RDS cluster will be created"
  type        = string
}

variable "db_subnet_group_name" {
  description = "Name of the DB subnet group"
  type        = string
}

variable "vpc_security_group_ids" {
  description = "List of VPC security group IDs"
  type        = list(string)
  default     = []
}

variable "vpc_cidr_blocks" {
  description = "List of VPC CIDR blocks"
  type        = list(string)
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

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}