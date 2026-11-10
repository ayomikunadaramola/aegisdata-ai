# AegisData AI — Terraform S3 starter

This Terraform configuration defines **one private S3 bucket**, account/region-based naming, SSE-S3 encryption, public-access blocking, bucket-owner-enforced object ownership, and a multipart-upload cleanup rule. It does **not** upload data, provision compute, or create Glue/Athena resources.

## Important safeguards
- Verify AWS budget email notifications; budgets are alerts, **not spending caps**.
- Check AWS promotional credit eligibility and remaining balance.
- Do not apply until you have reviewed the Terraform plan and requested cloud resources.
- The bucket has `force_destroy = false`, so nonempty buckets cannot be destroyed accidentally. Empty it deliberately when ready for teardown.
- Terraform uses **local state**. Keep `.terraform/`, `*.tfstate*`, and `terraform.tfvars` out of Git.
- A resource precondition blocks deployment if the authenticated AWS account does not match `expected_account_id`. Also verify STS identity manually.

## Set up on Windows CMD
From the repository root:

```cmd
copy infrastructure\terraform\terraform.tfvars.example infrastructure\terraform\terraform.tfvars
notepad infrastructure\terraform\terraform.tfvars
aws sts get-caller-identity --profile aegisdata
cd infrastructure\terraform
terraform fmt
terraform init
terraform validate
terraform plan -out=tfplan
```

Set the `expected_account_id` field to the account ID shown by STS before using `terraform plan`. **Do not run `terraform apply` until reviewing the plan and confirming costs.**

Avoid committing generated `tfplan`, local state, or cached credentials.

## After approval

```cmd
terraform apply tfplan
terraform output bucket_name
```

When the demonstration is complete, back up any needed evidence and delete data objects before `terraform destroy`. **Never run recursive deletion against an unverified bucket name.**
