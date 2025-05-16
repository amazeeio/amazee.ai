terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
      configuration_aliases = [aws]
    }
  }
}

data "aws_availability_zones" "available" {
  provider = aws
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  providers = {
    aws = aws
  }

  name = var.vpc_name
  cidr = var.vpc_cidr
  azs  = local.azs

  # Define subnets based on these AZs and the VPC CIDR
  public_subnets   = [for k, az_name in local.azs : cidrsubnet(var.vpc_cidr, 8, k)]
  private_subnets  = [for k, az_name in local.azs : cidrsubnet(var.vpc_cidr, 8, k + 3)] # Offset for private
  database_subnets = [for k, az_name in local.azs : cidrsubnet(var.vpc_cidr, 8, k + 6)] # Offset for database

  create_database_subnet_route_table     = true
  create_database_internet_gateway_route = true

  # Enable DNS hostnames and support, common for VPCs hosting RDS
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = var.tags
}

# Generate instance configurations based on count
locals {
  azs  = slice(data.aws_availability_zones.available.names, 0, 3)
  generate_instances = {
    for cluster_key, cluster in var.clusters : cluster_key => {
      for idx in range(cluster.instance_count) :
      "instance${idx + 1}" => local.default_instance_config
    }
  }
}

# Multiple postgres clusters in the same VPC
module "postgres_cluster" {
  source = "../postgres_cluster"
  for_each = var.clusters

  providers = {
    aws = aws
  }

  name               = each.key
  availability_zones = local.azs

  vpc_id               = module.vpc.vpc_id
  db_subnet_group_name = module.vpc.database_subnet_group_name
  vpc_cidr_blocks      = module.vpc.private_subnets_cidr_blocks

  instances           = local.generate_instances[each.key]
  min_capacity        = each.value.min_capacity
  max_capacity        = each.value.max_capacity
  backup_window       = each.value.backup_window
  maintenance_window  = each.value.maintenance_window

  tags = var.tags
}