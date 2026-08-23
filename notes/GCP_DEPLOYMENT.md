# GCP Deployment Runbook (Cloud Run + GitHub Actions CI/CD)

**Status:** pipeline live — first deploy triggered by operator request.
Target: Cloud Run (managed), Artifact Registry, Secret Manager, GCS for state
snapshots, Workload Identity Federation (no service-account keys).

## 0. Architecture

```
GitHub main ──> Actions: pytest (CI) ──> Actions: gcloud builds submit (CD)
                                             │
                                    Artifact Registry (docker image)
                                             │
                                      Cloud Run "venturebot"
                                       max-instances = 1
                                       secrets from Secret Manager
                                       data/ snapshots <-> GCS bucket
```

**State persistence (the honest caveat):** Cloud Run instances are ephemeral.
SQLite cannot live on a GCS FUSE mount (no locking), and Cloud SQL is
overkill pre-Phase-B. Interim solution: `max-instances=1` + `scripts/data_snapshot.py`
restores the latest snapshot from `gs://$GCS_DATA_BUCKET` at boot and re-uploads
every 5 min (`GCS_SYNC_SECONDS`). Worst case on a hard crash: ~5 min of state.
This becomes moot once Phase B (backend amnesia) removes persistent storage.

## 1. Create the infrastructure (one-time)

```bash
gcloud config set project YOUR_PROJECT_ID          # or create: gcloud projects create ...
gcloud billing projects link YOUR_PROJECT_ID --billing-account=BILLING_ID

# APIs
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
    iamcredentials.googleapis.com secretmanager.googleapis.com \
    storage.googleapis.com cloudbuild.googleapis.com

# Artifact Registry
gcloud artifacts repositories create venturebot \
    --repository-format=docker --location=europe-west1

# Data bucket
gsutil mb -l europe-west1 gs://YOUR_PROJECT_ID-venturebot-data

# Secrets (values from your .env — never commit them)
echo -n "$GOOGLE_API_KEY" | gcloud secrets create GOOGLE_API_KEY --data-file=-
echo -n "$GOOGLE_CLIENT_SECRET" | gcloud secrets create GOOGLE_CLIENT_SECRET --data-file=-
```

## 2. Runtime + deployer service accounts

```bash
# Runtime identity of the Cloud Run service
gcloud iam service-accounts create vb-runtime
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:vb-runtime@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
gsutil iam ch serviceAccount:vb-runtime@YOUR_PROJECT_ID.iam.gserviceaccount.com:objectAdmin \
    gs://YOUR_PROJECT_ID-venturebot-data

# Deployer identity used by GitHub Actions
gcloud iam service-accounts create vb-deploy
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:vb-deploy@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/run.admin"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:vb-deploy@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/iam.serviceAccountUser"      # may set runtime SA on the service
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:vb-deploy@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:vb-deploy@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/cloudbuild.builds.editor"
```

## 3. Workload Identity Federation (GitHub Actions -> GCP, keyless)

```bash
gcloud iam workload-identity-pools create github \
    --location=global --display-name="github"

gcloud iam workload-identity-pools providers create-oidc github \
    --workload-identity-pool=github \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --location=global \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository=='tdk67/venturebot'"

POOL_NUM=$(gcloud iam workload-identity-pools describe github \
    --location=global --format='value(name)' | cut -d/ -f5)
gcloud iam service-accounts add-iam-policy-binding \
    vb-deploy@YOUR_PROJECT_ID.iam.gserviceaccount.com \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/YOUR_PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/tdk67/venturebot"
```

## 4. Configure the GitHub repo

Settings → Secrets and variables → Actions → **Variables**:

| Name | Value |
|---|---|
| `GCP_PROJECT` | `YOUR_PROJECT_ID` |
| `GCP_REGION` | `europe-west1` |
| `GCP_WIF_PROVIDER` | `projects/YOUR_PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github` |
| `GCP_DEPLOY_SA` | `vb-deploy@YOUR_PROJECT_ID.iam.gserviceaccount.com` |
| `GCP_DATA_BUCKET` | `YOUR_PROJECT_ID-venturebot-data` |

Secrets (`GOOGLE_API_KEY`, `GOOGLE_CLIENT_SECRET`) live only in **Secret Manager**
(created in step 1) — the workflow passes them via `--set-secrets`.

As soon as `GCP_PROJECT` exists, every push to `main` deploys automatically
(`deploy.yml` skips with a notice until then; `ci.yml` runs regardless).

## 5. First deploy & OAuth wiring

1. Push to `main` (or run the *Deploy* workflow manually).
2. Get the service URL:
   ```bash
   gcloud run services describe venturebot --region europe-west1 --format='value(status.url)'
   ```
3. Add `<URL>/api/auth/callback` as an **Authorized redirect URI** in the Google
   OAuth client (same place as the production domain's URI).
4. Set the public base URL so OAuth builds correct redirect URIs:
   ```bash
   gcloud run services update venturebot --region europe-west1 \
       --update-env-vars=VENTUREBOT_PUBLIC_BASE_URL=<URL>
   ```
   (Or map a custom domain first and use that instead.)
5. Log in via Google. Verify data round-trips: submit an idea, redeploy
   (`workflow_dispatch`), check the idea survived (snapshot restore works).

## 6. Cost / limits sanity

- Cloud Run scale-to-zero, 1 vCPU/1Gi, max-instances=1: pennies/month at low traffic
- Artifact Registry: ~0.1 GB/image layer reuse; prune old images occasionally
- LLM spend is unchanged (Gemini API billed separately); budget cap still enforced in-app

## 7. Known gaps

- Single instance until Phase B — horizontal scaling needs real persistent
  storage (Cloud SQL) or backend amnesia
- `sandbox.py` (unshare/setuid pytest isolation) is inert on Cloud Run — fine
  today since Phase 2 (blind TDD) is not deployed; revisit before enabling it there
- No staging environment: `main` deploys straight to prod (fine at this scale;
  add a `-staging` service later if needed)
