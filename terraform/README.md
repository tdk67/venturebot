# VentureBot — Terraform Quick-Start

## One-time setup

```bash
# 1. Install Terraform (if not installed)
#    https://developer.hashicorp.com/terraform/downloads

# 2. Authenticate as project owner
gcloud auth login
gcloud auth application-default login
gcloud config set project venturebot-506408

# 3. Deploy
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — fill in your secrets, client ID, etc.

terraform init
terraform plan    # review what will be created
terraform apply   # deploy everything
```

## What it creates

| Resource | Terraform resource | Details |
|---|---|---|
| 7 APIs | `google_project_service` | run, artifactregistry, secretmanager, cloudbuild, etc. |
| 2 service accounts | `google_service_account` | vb-runtime, vb-deploy |
| 10 IAM bindings | `google_project_iam_member` | All roles for both SAs |
| Artifact Registry | `google_artifact_registry_repository` | Docker repo, europe-west3 |
| 2 secrets | `google_secret_manager_secret` | GOOGLE_API_KEY, GOOGLE_CLIENT_SECRET |
| GCS bucket | `google_storage_bucket` | Data snapshots |
| WIF pool + provider | `google_iam_workload_identity_pool*` | GitHub Actions → GCP |
| Cloud Run service | `google_cloud_run_v2_service` | venturebot, max-instances=1 |

## Daily use

```bash
terraform plan    # see what changed (drift detection)
terraform apply   # apply changes
terraform destroy # tear EVERYTHING down (careful!)
```

## Adding a staging environment

```bash
cp terraform.tfvars terraform-staging.tfvars
# Edit: change project_id, public_base_url, etc.

terraform workspace new staging
terraform apply -var-file=terraform-staging.tfvars
```

## State management for teams

The state file (`terraform.tfstate`) is local by default. For teams, move it to GCS:

```bash
# In main.tf, add before the provider block:
# terraform {
#   backend "gcs" {
#     bucket = "venturebot-506408-terraform-state"
#     prefix = "terraform/state"
#   }
# }
```

Then: `terraform init -migrate-state`