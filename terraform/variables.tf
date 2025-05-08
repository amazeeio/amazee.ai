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

variable "allowed_assume_role_ips" {
  description = "List of IP addresses allowed to assume the role"
  type        = list(string)
  default     = ["197.83.234.246"]
}
