#!/usr/bin/env bash
# ============================================================================
# VentureBot — One-Shot GCP Deploy Setup
# ============================================================================
# Runs on your LOCAL machine (not Cloud Shell, not the VPS).
# Prerequisites:
#   1. Install gcloud: https://cloud.google.com/sdk/docs/install
#   2. gcloud auth login              (your Google account, must be project owner)
#   3. gcloud config set project YOUR_PROJECT_ID
#   4. chmod +x setup.sh && ./setup.sh
#
# This script is idempotent — run it as many times as you want.
# ============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1"; exit 1; }

# ── Config (change these) ───────────────────────────────────────────────────

PROJECT_ID="${GCP_PROJECT:-venturebot-506408}"
REGION="${GCP_REGION:-europe-west3}"
GITHUB_REPO="${GITHUB_REPO:-tdk67/venturebot}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://venturebot-442488405067.europe-west3.run.app}"
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-442488405067-danioee2r9r0hrih94j6d7rjt6lraeoa.apps.googleusercontent.com}"
GOOGLE_API_KEY="${GOOGLE_API_KEY:-YOUR_GEMINI_API_KEY}"
GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-YOUR_GOOGLE_CLIENT_SECRET}"

# ── Derived ─────────────────────────────────────────────────────────────────

RUNTIME_SA="vb-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOY_SA="vb-deploy@${PROJECT_ID}.iam.gserviceaccount.com"
DATA_BUCKET="${PROJECT_ID}-venturebot-data"

echo "=========================================="
echo " VentureBot GCP Setup"
echo " Project: $PROJECT_ID"
echo " Region:  $REGION"
echo "=========================================="
echo ""

# ── 0. Verify authentication ────────────────────────────────────────────────

ACTIVE_ACCOUNT=$(gcloud config get account 2>/dev/null || echo "")
if [ -z "$ACTIVE_ACCOUNT" ]; then
  err "Not authenticated. Run: gcloud auth login"
fi
if [ "$(gcloud config get project 2>/dev/null)" != "$PROJECT_ID" ]; then
  err "Wrong project. Run: gcloud config set project $PROJECT_ID"
fi
ok "Authenticated as $ACTIVE_ACCOUNT"

# ── 1. Enable APIs ──────────────────────────────────────────────────────────

echo ""
echo "── 1. Enabling APIs ──"
for api in \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  iamcredentials.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  cloudbuild.googleapis.com \
  cloudresourcemanager.googleapis.com; do
  gcloud services enable "$api" --project="$PROJECT_ID" --quiet 2>/dev/null && ok "$api" || warn "$api (already enabled)"
done

# ── 2. Service accounts ─────────────────────────────────────────────────────

echo ""
echo "── 2. Service accounts ──"

create_sa() {
  local id="$1" display="$2"
  if gcloud iam service-accounts describe "${id}@${PROJECT_ID}.iam.gserviceaccount.com" \
      --project="$PROJECT_ID" &>/dev/null; then
    ok "${id} exists"
  else
    gcloud iam service-accounts create "$id" --display-name="$display" --project="$PROJECT_ID"
    ok "${id} created"
  fi
}
create_sa "vb-runtime" "VentureBot Cloud Run runtime"
create_sa "vb-deploy"  "VentureBot CI/CD deployer"

# ── 3. IAM roles ────────────────────────────────────────────────────────────

echo ""
echo "── 3. IAM roles ──"

grant() {
  local member="$1" role="$2"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="$member" --role="$role" --quiet 2>/dev/null && ok "$role → $(echo $member | cut -d: -f2-)" || warn "$role → already granted"
}

# vb-runtime roles
grant "serviceAccount:${RUNTIME_SA}" "roles/secretmanager.secretAccessor"
grant "serviceAccount:${RUNTIME_SA}" "roles/storage.objectAdmin"

# vb-deploy roles (everything CI/CD needs)
for role in \
  roles/run.admin \
  roles/iam.serviceAccountUser \
  roles/artifactregistry.writer \
  roles/cloudbuild.builds.editor \
  roles/storage.admin \
  roles/serviceusage.serviceUsageConsumer \
  roles/secretmanager.secretAccessor \
  roles/viewer; do
  grant "serviceAccount:${DEPLOY_SA}" "$role"
done

# ── 4. Storage bucket for Cloud Build ───────────────────────────────────────

echo ""
echo "── 4. Storage ──"

if gcloud storage buckets describe "gs://${DATA_BUCKET}" &>/dev/null 2>&1; then
  ok "Data bucket: $DATA_BUCKET"
else
  gcloud storage buckets create "gs://${DATA_BUCKET}" --location="$REGION" --project="$PROJECT_ID"
  ok "Data bucket created"
fi

# Grant vb-runtime access to the data bucket
gcloud storage buckets add-iam-policy-binding "gs://${DATA_BUCKET}" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/storage.objectAdmin" --quiet 2>/dev/null || true
ok "vb-runtime → objectAdmin on $DATA_BUCKET"

# ── 5. Artifact Registry ────────────────────────────────────────────────────

echo ""
echo "── 5. Artifact Registry ──"

if gcloud artifacts repositories describe venturebot --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
  ok "Artifact Registry: venturebot"
else
  gcloud artifacts repositories create venturebot \
    --repository-format=docker --location="$REGION" --project="$PROJECT_ID"
  ok "Artifact Registry created"
fi

# ── 6. Secrets ──────────────────────────────────────────────────────────────

echo ""
echo "── 6. Secret Manager ──"

create_secret() {
  local name="$1" value="$2"
  if gcloud secrets describe "$name" --project="$PROJECT_ID" &>/dev/null 2>&1; then
    ok "Secret: $name"
  else
    echo -n "$value" | gcloud secrets create "$name" --data-file=- --project="$PROJECT_ID"
    ok "Secret created: $name"
  fi
}
create_secret "GOOGLE_API_KEY" "$GOOGLE_API_KEY"
create_secret "GOOGLE_CLIENT_SECRET" "$GOOGLE_CLIENT_SECRET"

# ── 7. Workload Identity Federation ─────────────────────────────────────────

echo ""
echo "── 7. Workload Identity Federation ──"

PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

if gcloud iam workload-identity-pools describe github-pool --location=global --project="$PROJECT_ID" &>/dev/null; then
  ok "WIF pool: github-pool"
else
  gcloud iam workload-identity-pools create github-pool \
    --location=global --display-name="GitHub Actions" --project="$PROJECT_ID"
  ok "WIF pool created"
fi

if gcloud iam workload-identity-pools providers describe github-provider \
    --workload-identity-pool=github-pool --location=global --project="$PROJECT_ID" &>/dev/null; then
  ok "WIF provider: github-provider"
else
  gcloud iam workload-identity-pools providers create-oidc github-provider \
    --workload-identity-pool=github-pool --location=global \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository=='${GITHUB_REPO}'" \
    --project="$PROJECT_ID"
  ok "WIF provider created"
fi

# WIF binding to vb-deploy
WIF_MEMBER="principalSet://iam.googleapis.com/projects/${PROJECT_NUM}/locations/global/workloadIdentityPools/github-pool/attribute.repository/${GITHUB_REPO}"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --role="roles/iam.workloadIdentityUser" --member="$WIF_MEMBER" \
  --project="$PROJECT_ID" --quiet 2>/dev/null && ok "WIF binding" || warn "WIF binding (already set)"

# ── 8. Cloud Run service (initial deploy) ───────────────────────────────────

echo ""
echo "── 8. Cloud Run ──"

SERVICE_EXISTS=$(gcloud run services describe venturebot --region="$REGION" --project="$PROJECT_ID" 2>/dev/null && echo "yes" || echo "no")

if [ "$SERVICE_EXISTS" = "yes" ]; then
  echo "   Updating environment variables..."
  gcloud run services update venturebot --region="$REGION" --project="$PROJECT_ID" \
    --update-env-vars="VENTUREBOT_PUBLIC_BASE_URL=${PUBLIC_BASE_URL},GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID},VENTUREBOT_NO_AUTH=0,VENTUREBOT_COOKIE_SECURE=true,GCS_DATA_BUCKET=${DATA_BUCKET}" \
    --quiet 2>/dev/null
  ok "Cloud Run service updated"
else
  warn "Cloud Run service not created yet — first deploy via GitHub Actions will create it"
  warn "Or push a placeholder image first, then run this script again"
fi

# ── 9. Summary ──────────────────────────────────────────────────────────────

SERVICE_URL=$(gcloud run services describe venturebot --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)" 2>/dev/null || echo "N/A")

echo ""
echo "=========================================="
echo " ${GREEN}Setup Complete!${NC}"
echo "=========================================="
echo ""
echo "GitHub Actions variables (add to repo settings):"
echo "  GCP_PROJECT           = $PROJECT_ID"
echo "  GCP_REGION            = $REGION"
echo "  GCP_WIF_PROVIDER      = projects/$PROJECT_NUM/locations/global/workloadIdentityPools/github-pool/providers/github-provider"
echo "  GCP_DEPLOY_SA         = $DEPLOY_SA"
echo "  GCP_DATA_BUCKET       = $DATA_BUCKET"
echo ""
echo "Service URL: $SERVICE_URL"
echo ""
echo "After first deploy, add this to your Google OAuth client redirect URIs:"
echo "  ${SERVICE_URL}/api/auth/callback"
echo "  (or your custom domain + /api/auth/callback)"
echo ""
echo "Then push to main — deploys automatically!"