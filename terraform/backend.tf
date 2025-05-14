terraform {
  backend "s3" {
    bucket       = "amazeeai-terraform-state-dev"
    key          = "terraform.tfstate"
    region       = "eu-central-2"
    use_lockfile = true
    encrypt      = true
  }
}