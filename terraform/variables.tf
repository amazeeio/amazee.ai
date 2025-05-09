variable "aws_account_id" {
  description = "The AWS account ID"
  type        = string
}

variable "aws_region" {
  description = "The AWS region to deploy resources"
  type        = string
  default     = "eu-west-1"
}

variable "tags" {
  description = "A map of tags to add to all resources"
  type        = map(string)
  default = {
    project     = "amazee-ai"
    environment = "dev"
  }
}

variable "environment_suffix" {
  description = "Suffix to append to resource names"
  type        = string
  default     = "dev"
}

variable "domain_name" {
  description = "The domain name to use for SES identity"
  type        = string
  default     = "ai.amazee.io"
}

variable "dkim_private_key" {
  description = "The private key to use for DKIM signing"
  type        = string
  sensitive   = true
}
