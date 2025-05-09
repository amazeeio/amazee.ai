variable "aws_account_id" {
  description = "The AWS account ID - must be the numeric digits and not an alias"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "The aws_account_id value must be a 12-digit number."
  }
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
