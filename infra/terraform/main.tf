terraform {
  required_version = ">= 1.5.0"
  
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  
  backend "s3" {
    bucket = "ai-agent-platform-terraform-state"
    key    = "prod/terraform.tfstate"
    region = "us-east-1"
    encrypt = true
    dynamodb_table = "terraform-state-lock"
  }
}

provider "aws" {
  region = var.aws_region
  
  default_tags {
    tags = {
      Project     = "ai-agent-platform"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# Variables
variable "aws_region" {
  default = "us-east-1"
}

variable "environment" {
  default = "dev"
}

variable "project_name" {
  default = "ai-agent-platform"
}

# Network Module
module "network" {
  source = "./modules/network"
  
  project_name = var.project_name
  environment  = var.environment
}

# Database Module
module "database" {
  source = "./modules/database"
  
  project_name        = var.project_name
  environment         = var.environment
  vpc_id              = module.network.vpc_id
  private_subnet_ids  = module.network.private_subnet_ids
  db_security_group_id = module.network.db_security_group_id
}

# S3 Module
module "storage" {
  source = "./modules/storage"
  
  project_name = var.project_name
  environment  = var.environment
}

# SQS Module
module "messaging" {
  source = "./modules/messaging"
  
  project_name = var.project_name
  environment  = var.environment
}

# ECS Cluster
module "compute" {
  source = "./modules/compute"
  
  project_name           = var.project_name
  environment            = var.environment
  vpc_id                 = module.network.vpc_id
  private_subnet_ids     = module.network.private_subnet_ids
  public_subnet_ids      = module.network.public_subnet_ids
  alb_security_group_id  = module.network.alb_security_group_id
  ecs_security_group_id  = module.network.ecs_security_group_id
  
  database_url           = module.database.database_url
  redis_url              = "redis://localhost:6379"
  sqs_queue_url          = module.messaging.queue_url
  s3_bucket_name         = module.storage.artifacts_bucket_name
}

# Outputs
output "alb_dns_name" {
  value = module.compute.alb_dns_name
}

output "database_endpoint" {
  value = module.database.endpoint
}

output "sqs_queue_url" {
  value = module.messaging.queue_url
}

output "s3_bucket_name" {
  value = module.storage.artifacts_bucket_name
}