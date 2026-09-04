data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ec2_managed_prefix_list" "cloudfront_origin" {
  filter {
    name   = "prefix-list-name"
    values = ["com.amazonaws.global.cloudfront.origin-facing"]
  }
}

resource "aws_vpc" "generated" {
  count                = var.create_network ? 1 : 0
  cidr_block           = var.network_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "${var.name}-vpc"
  }
}

resource "aws_internet_gateway" "generated" {
  count  = var.create_network ? 1 : 0
  vpc_id = aws_vpc.generated[0].id

  tags = {
    Name = "${var.name}-igw"
  }
}

resource "aws_subnet" "worker" {
  count                   = var.create_network ? 2 : 0
  vpc_id                  = aws_vpc.generated[0].id
  cidr_block              = cidrsubnet(var.network_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.name}-worker-${count.index + 1}"
  }
}

resource "aws_subnet" "database" {
  count                   = var.create_network ? 2 : 0
  vpc_id                  = aws_vpc.generated[0].id
  cidr_block              = cidrsubnet(var.network_cidr, 8, count.index + 16)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.name}-database-${count.index + 1}"
  }
}

resource "aws_route_table" "public" {
  count  = var.create_network ? 1 : 0
  vpc_id = aws_vpc.generated[0].id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.generated[0].id
  }

  tags = {
    Name = "${var.name}-public"
  }
}

resource "aws_route_table_association" "worker" {
  count          = var.create_network ? 2 : 0
  subnet_id      = aws_subnet.worker[count.index].id
  route_table_id = aws_route_table.public[0].id
}

resource "aws_security_group" "worker" {
  count       = var.create_network ? 1 : 0
  name        = "${var.name}-worker"
  description = "Egress-only security group for LingoWave GPU workers"
  vpc_id      = aws_vpc.generated[0].id

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "load_balancer" {
  count       = var.create_network && var.api_image != "" ? 1 : 0
  name        = "${var.name}-api-alb"
  description = "Public HTTP access to the LingoWave API load balancer"
  vpc_id      = aws_vpc.generated[0].id

  ingress {
    protocol        = "tcp"
    from_port       = 80
    to_port         = 80
    cidr_blocks     = var.frontend_enabled && var.api_certificate_arn == "" ? [] : ["0.0.0.0/0"]
    prefix_list_ids = var.frontend_enabled && var.api_certificate_arn == "" ? [data.aws_ec2_managed_prefix_list.cloudfront_origin.id] : []
  }

  ingress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "api" {
  count       = var.create_network && var.api_image != "" ? 1 : 0
  name        = "${var.name}-api"
  description = "Egress-only API task security group with traffic from the load balancer"
  vpc_id      = aws_vpc.generated[0].id

  ingress {
    protocol        = "tcp"
    from_port       = 8000
    to_port         = 8000
    security_groups = [aws_security_group.load_balancer[0].id]
    description     = "API traffic from the public load balancer"
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "database" {
  count       = var.create_network ? 1 : 0
  name        = "${var.name}-database"
  description = "PostgreSQL access from LingoWave workers"
  vpc_id      = aws_vpc.generated[0].id

  ingress {
    protocol        = "tcp"
    from_port       = 5432
    to_port         = 5432
    security_groups = var.api_image != "" ? [aws_security_group.worker[0].id, aws_security_group.api[0].id] : [aws_security_group.worker[0].id]
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}

locals {
  effective_vpc_id                          = var.create_network ? aws_vpc.generated[0].id : var.vpc_id
  effective_worker_subnet_ids               = var.create_network ? aws_subnet.worker[*].id : var.worker_subnet_ids
  effective_worker_security_group_id        = var.create_network ? aws_security_group.worker[0].id : var.worker_security_group_id
  effective_api_subnet_ids                  = var.create_network ? aws_subnet.worker[*].id : var.api_subnet_ids
  effective_api_security_group_id           = var.create_network ? (var.api_image != "" ? aws_security_group.api[0].id : "") : var.api_security_group_id
  effective_load_balancer_security_group_id = var.create_network ? (var.api_image != "" ? aws_security_group.load_balancer[0].id : "") : var.load_balancer_security_group_id
  effective_database_subnet_ids             = var.create_network ? aws_subnet.database[*].id : var.database_subnet_ids
  effective_database_security_group_id      = var.create_network ? aws_security_group.database[0].id : var.database_security_group_id
}
