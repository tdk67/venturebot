# ============================================================================
# VentureBot — GCP Infrastructure (Terraform)
# ============================================================================
# One command: terraform apply
# No clicking. No Cloud Shell. No manual IAM.
#
# Prerequisites: authenticated gcloud with owner on the project.
#   gcloud auth login
#   gcloud config set project YOUR_PROJECT_ID
# ============================================================================

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ── APIs ────────────────────────────────────────────────────────────────────

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "iamcredentials.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])
  service                    = each.key
  disable_dependent_services = false
  disable_on_destroy         = false
}

# ── Service Accounts ────────────────────────────────────────────────────────

resource "google_service_account" "vb_runtime" {
  account_id   = "vb-runtime"
  display_name = "VentureBot Cloud Run runtime"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "vb_deploy" {
  account_id   = "vb-deploy"
  display_name = "VentureBot CI/CD deployer"
  depends_on   = [google_project_service.apis]
}

# ── IAM: vb-runtime (what the Cloud Run service itself needs) ───────────────

resource "google_project_iam_member" "runtime_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.vb_runtime.email}"
}

# ── IAM: vb-deploy (what GitHub Actions CI/CD needs) ────────────────────────

locals {
  vb_deploy_roles = [
    "roles/run.admin",
    "roles/iam.serviceAccountUser",
    "roles/artifactregistry.writer",
    "roles/cloudbuild.builds.editor",
    "roles/storage.admin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/secretmanager.secretAccessor",
    "roles/viewer",
  ]
}

resource "google_project_iam_member" "deploy_roles" {
  for_each = toset(local.vb_deploy_roles)
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.vb_deploy.email}"
}

# ── Artifact Registry ───────────────────────────────────────────────────────

resource "google_artifact_registry_repository" "venturebot" {
  location      = var.region
  repository_id = "venturebot"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# ── Secret Manager ──────────────────────────────────────────────────────────

resource "google_secret_manager_secret" "google_api_key" {
  secret_id = "GOOGLE_API_KEY"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "google_client_secret" {
  secret_id = "GOOGLE_CLIENT_SECRET"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# ── GCS Data Bucket ─────────────────────────────────────────────────────────

resource "google_storage_bucket" "data" {
  name          = "${var.project_id}-venturebot-data"
  location      = var.region
  force_destroy = false
  depends_on    = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "runtime_data_admin" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.vb_runtime.email}"
}

# ── Workload Identity Federation ────────────────────────────────────────────

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub Actions OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository=='${var.github_repo}'"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "wif_binding" {
  service_account_id = google_service_account.vb_deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# ── Cloud Run Service (placeholder — CI/CD redeploys on every push) ─────────

resource "google_cloud_run_v2_service" "venturebot" {
  name     = "venturebot"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.vb_runtime.email
    scaling {
      max_instance_count = 1
      min_instance_count = 0
    }
    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/venturebot/venturebot:placeholder"
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }
      env {
        name  = "VENTUREBOT_NO_AUTH"
        value = "0"
      }
      env {
        name  = "VENTUREBOT_COOKIE_SECURE"
        value = "true"
      }
      env {
        name  = "GCS_DATA_BUCKET"
        value = google_storage_bucket.data.name
      }
      env {
        name  = "VENTUREBOT_PUBLIC_BASE_URL"
        value = var.public_base_url
      }
      env {
        name  = "GOOGLE_CLIENT_ID"
        value = var.google_client_id
      }
      env {
        name = "GOOGLE_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GOOGLE_CLIENT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.google_client_secret.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_project_iam_member.runtime_secret_accessor,
  ]

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,  # CI/CD updates the image
    ]
  }
}

resource "google_cloud_run_service_iam_member" "public" {
  location = google_cloud_run_v2_service.venturebot.location
  project  = var.project_id
  service  = google_cloud_run_v2_service.venturebot.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}