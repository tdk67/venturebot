variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "europe-west3"
}

variable "github_repo" {
  description = "GitHub repo in owner/name format (for WIF)"
  type        = string
}

variable "public_base_url" {
  description = "Public URL of the Cloud Run service (set after first deploy)"
  type        = string
  default     = ""
}

variable "google_client_id" {
  description = "Google OAuth client ID"
  type        = string
  default     = ""
}

variable "google_api_key" {
  description = "Gemini API key (stored in Secret Manager)"
  type        = string
  sensitive   = true
}

variable "google_client_secret" {
  description = "Google OAuth client secret (stored in Secret Manager)"
  type        = string
  sensitive   = true
}