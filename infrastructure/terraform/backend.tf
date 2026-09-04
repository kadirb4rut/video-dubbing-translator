terraform {
  backend "s3" {
    bucket         = "lingowave-terraform-state-e7879804"
    key            = "lingowave/terraform.tfstate"
    region         = "eu-north-1"
    dynamodb_table = "lingowave-terraform-lock"
    encrypt        = true
  }
}
