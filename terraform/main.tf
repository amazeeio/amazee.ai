terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

locals {
  trust_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_user.role_assumer.arn
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  is_production_environment = contains(["dev", "main", "prod"], var.environment_suffix)
}

resource "aws_iam_user" "role_assumer" {
  name = "amazeeai-role-assumer-${var.environment_suffix}"
  tags = var.tags
}

resource "aws_iam_access_key" "role_assumer" {
  user = aws_iam_user.role_assumer.name
}

resource "aws_iam_user_policy" "role_assumer" {
  name = "role-assumption-policy-${var.environment_suffix}"
  user = aws_iam_user.role_assumer.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AssumeAnyRole"
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = "arn:aws:iam::${var.aws_account_id}:role/*"
      },
      {
        Sid      = "GetCallerIdentity"
        Effect   = "Allow"
        Action   = "sts:GetCallerIdentity"
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role" "amazeeai_send_email" {
  name               = "amazeeai-send-email-${var.environment_suffix}"
  tags               = var.tags
  assume_role_policy = local.trust_policy
}

resource "aws_iam_role_policy" "amazeeai_send_email" {
  name = "amazeeai-send-email-policy-${var.environment_suffix}"
  role = aws_iam_role.amazeeai_send_email.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ResourceRestricted"
        Effect = "Allow"
        Action = [
          "ses:SendEmail",
          "ses:SendTemplatedEmail",
          "ses:UpdateEmailTemplate",
          "ses:GetEmailTemplate",
          "ses:CreateEmailTemplate",
          "ses:DeleteEmailTemplate"
        ]
        Resource = [
          "arn:aws:ses:*:${var.aws_account_id}:template/*",
          "arn:aws:ses:*:${var.aws_account_id}:identity/*"
        ]
      },
      {
        Sid    = "List"
        Effect = "Allow"
        Action = [
          "ses:ListEmailTemplates"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role" "amazeeai_ddb" {
  name               = "amazeeai-ddb-${var.environment_suffix}"
  tags               = var.tags
  assume_role_policy = local.trust_policy
}

resource "aws_iam_role_policy" "amazeeai_ddb" {
  name = "amazeeai-ddb-policy-${var.environment_suffix}"
  role = aws_iam_role.amazeeai_ddb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TableOperations"
        Effect = "Allow"
        Action = [
          "dynamodb:CreateTable",
          "dynamodb:UpdateTimeToLive",
          "dynamodb:PutItem",
          "dynamodb:DescribeTable",
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:UpdateItem",
          "dynamodb:UpdateTable"
        ]
        Resource = "arn:aws:dynamodb:*:${var.aws_account_id}:table/*"
      },
      {
        Sid      = "ListTables"
        Effect   = "Allow"
        Action   = "dynamodb:ListTables"
        Resource = "*"
      }
    ]
  })
}

# resource "aws_sesv2_email_identity" "domain" {
#   email_identity = var.domain_name
#   tags           = var.tags
#
#   dkim_signing_attributes {
#     domain_signing_selector    = "amazee"
#     domain_signing_private_key = var.tags
#   }
# }

resource "aws_dynamodb_table" "verification_codes" {
  name                        = "verification-codes-${var.environment_suffix}"
  billing_mode                = "PAY_PER_REQUEST"
  deletion_protection_enabled = local.is_production_environment

  hash_key = "email"
  attribute {
    name = "email"
    type = "S"
  }

  ttl {
    enabled        = true
    attribute_name = "ttl"
  }

  tags = var.tags
}
