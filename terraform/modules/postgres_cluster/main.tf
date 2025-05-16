terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
      configuration_aliases = [aws]
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

resource "random_password" "master_password" {
  length           = 16
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

module "aurora" {
  source  = "terraform-aws-modules/rds-aurora/aws"
  version = "~> 9.0"

  providers = {
    aws = aws
  }

  engine         = "aurora-postgresql"
  engine_version = "16.4"
  engine_mode    = "provisioned"

  availability_zones = var.availability_zones

  storage_encrypted   = true
  master_username     = "postgres"
  master_password     = random_password.master_password.result

  manage_master_user_password          = false
  manage_master_user_password_rotation = false

  vpc_id               = var.vpc_id
  db_subnet_group_name = var.db_subnet_group_name

  security_group_rules = {
    vpc_ingress = {
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  name = var.name

  instances = var.instances

  serverlessv2_scaling_configuration = {
    min_capacity = var.min_capacity
    max_capacity = var.max_capacity
  }

  preferred_backup_window      = var.backup_window
  preferred_maintenance_window = var.maintenance_window

  backup_retention_period = 7

  monitoring_interval    = 60
  create_monitoring_role = true

  performance_insights_enabled          = true
  performance_insights_retention_period = 7

  db_cluster_parameter_group_name    = "default.aurora-postgresql16"
  create_db_cluster_parameter_group  = false
  db_parameter_group_name           = "default.aurora-postgresql16"
  create_db_parameter_group         = false

  copy_tags_to_snapshot               = true
  deletion_protection                 = true
  iam_database_authentication_enabled = false
  network_type                        = "IPV4"
  enable_http_endpoint                = false
  auto_minor_version_upgrade          = true

  tags = var.tags
}