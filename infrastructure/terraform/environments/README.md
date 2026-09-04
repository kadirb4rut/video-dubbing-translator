# Terraform environments

Keep staging and production state in different S3 object keys. The backend
configuration is intentionally partial in `../backend.tf`; initialize the
desired environment explicitly:

```bash
terraform init -reconfigure \
  -backend-config=environments/backend-staging.hcl
terraform plan -var-file=environments/staging.tfvars
```

Use the production files for the production account/state only. Copy the
`.example` files, replace placeholders, and keep the copied files untracked.
Never put database passwords, API keys, Stripe secrets, or SES credentials in
these files; pass them through `TF_VAR_*` or inject them from the deployment
secret manager.

The production backend key preserves the currently deployed state location;
staging uses a separate key and must never be initialized against production.
