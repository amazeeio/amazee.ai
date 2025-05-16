module "vectordb_prod_eu_central_1" {
  source = "./modules/vectordb_region"
  count  = terraform.workspace == "prod" ? 1 : 0

  providers = {
    aws = aws.eu_central_1
  }

  vpc_name = "amazeeai-vectordb-vpc-${var.environment_suffix}"

  clusters  = {
    amazeeai-de103-vectordb1 = {
      instance_count   = 2
      min_capacity     = 2
      max_capacity     = 16
      backup_window    = "06:42-07:12"
      maintenance_window = "wed:04:35-wed:05:05"
    }
  }

  tags      = var.tags
}


module "vectordb_prod_eu_central_2" {
  source = "./modules/vectordb_region"
  count  = terraform.workspace == "prod" ? 1 : 0

  providers = {
    aws = aws.eu_central_2
  }

  vpc_name = "amazeeai-vectordb-vpc-${var.environment_suffix}"

  clusters  = {
    amazeeai-ch103-vectordb1 = {
      instance_count   = 2
      min_capacity     = 2
      max_capacity     = 16
      backup_window    = "06:42-07:12"
      maintenance_window = "wed:04:35-wed:05:05"
    }
  }

  tags      = var.tags
}

module "vectordb_prod_us_east_1" {
  source = "./modules/vectordb_region"
  count  = terraform.workspace == "prod" ? 1 : 0

  providers = {
    aws = aws.us_east_1
  }

  vpc_name = "amazeeai-vectordb-vpc-${var.environment_suffix}"

  clusters  = {
    amazeeai-us103-vectordb1 = {
      instance_count   = 2
      min_capacity     = 2
      max_capacity     = 16
      backup_window    = "06:42-07:12"
      maintenance_window = "wed:04:35-wed:05:05"
    }
  }

  tags      = var.tags
}

module "vectordb_prod_eu_west_2" {
  source = "./modules/vectordb_region"
  count  = terraform.workspace == "prod" ? 1 : 0

  providers = {
    aws = aws.eu_west_2
  }

  vpc_name = "amazeeai-vectordb-vpc-${var.environment_suffix}"

  clusters  = {
    amazeeai-uk103-vectordb1 = {
      instance_count   = 2
      min_capacity     = 2
      max_capacity     = 16
      backup_window    = "06:42-07:12"
      maintenance_window = "wed:04:35-wed:05:05"
    }
  }

  tags      = var.tags
}


module "vectordb_prod_ap_southeast_2" {
  source = "./modules/vectordb_region"
  count  = terraform.workspace == "prod" ? 1 : 0

  providers = {
    aws = aws.ap_southeast_2
  }

  vpc_name = "amazeeai-vectordb-vpc-${var.environment_suffix}"

  clusters  = {
    amazeeai-au103-vectordb1 = {
      instance_count   = 2
      min_capacity     = 2
      max_capacity     = 16
      backup_window    = "06:42-07:12"
      maintenance_window = "wed:04:35-wed:05:05"
    }
  }

  tags      = var.tags
}