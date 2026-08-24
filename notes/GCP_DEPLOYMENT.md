# GCP Deployment — Complete Guide

**One command, or one `terraform apply`. No clicking.**

Three approaches — pick one:

| Approach | Time | Prerequisites | Best for |
|---|---|---|---|
| **A: Setup script** | 5 min | `gcloud` CLI + owner login | Quick setup, one project |
| **B: Terraform** | 10 min | `terraform` + `gcloud` CLI | Teams, reproducibility, multiple envs |
| **C: Manual (UI)** | 30 min | Browser only | Last resort when CLI isn't available |

---

## A: Setup Script (recommended)

### Prerequisites

```bash
# Install gcloud CLI (one-time, local machine)
# https://cloud.google.com/sdk/docs/install
curl https://sdk.cloud.google.com | bash
exec -l $SHELL

# Authenticate as the project owner (your Google account, NOT a service account)
gcloud auth login
gcloud config set project venturebot-506408
```

### Run it

```bash
cd venturebot/scripts
./setup.sh
```

That's it. The script is **idempotent** — run it as many times as you want. It creates everything: APIs, service accounts, IAM roles, secrets, Artifact Registry, GCS bucket, and Workload Identity Federation.

### What it creates

```
✅ APIs enabled (run, artifactregistry, secretmanager, cloudbuild, etc.)
✅ Service accounts: vb-runtime (Cloud Run), vb-deploy (GitHub Actions)
✅ IAM roles for both
✅ Artifact Registry: venturebot (Docker)
✅ Secrets: GOOGLE_API_KEY, GOOGLE_CLIENT_SECRET
✅ GCS bucket: {project}-venturebot-data
✅ WIF pool + provider + binding to vb-deploy
✅ Cloud Run service config (env vars, secrets, scaling)
```

### What you still do manually (one-time, 2 minutes)

1. **Add OAuth redirect URI:** Open your Google OAuth client → add `https://YOUR_SERVICE_URL/api/auth/callback`
2. **GitHub repo variables:** Add the 5 variables the script prints at the end

---

## B: Terraform (infrastructure-as-code)

### Prerequisites

```bash
# Install terraform (one-time)
# https://developer.hashicorp.com/terraform/downloads

# Install gcloud and authenticate
gcloud auth login
gcloud auth application-default login
```

### Run it

```bash
cd venturebot/terraform

# Copy and fill in your values
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your project ID, secrets, etc.

# One-shot
terraform init
terraform plan
terraform apply
```

### What Terraform gives you

- **State file:** Know exactly what's deployed and what changed
- **Drift detection:** `terraform plan` shows what drifted from the desired state
- **Review before apply:** See the full diff before making changes
- **Destroy:** `terraform destroy` cleans up everything
- **Teams:** Commit the `.tf` files, state goes to a GCS backend
- **Multiple environments:** Same code, different `tfvars` → staging, prod

### Terraform vs setup script

The setup script is simpler (no Terraform state to manage). Terraform is better for teams and long-term maintenance. Both produce the same result. If you're not sure, start with the script — you can always import the resources into Terraform later.

---

## C: Manual (UI only) — last resort

If you must use the console, follow these exact steps:

### 1. Enable APIs

**URL:** `https://console.cloud.google.com/apis/library?project=YOUR_PROJECT`

Search and enable these 7 APIs:
- Cloud Run API
- Artifact Registry API
- IAM Service Account Credentials API
- Secret Manager API
- Cloud Storage API
- Cloud Build API
- Cloud Resource Manager API

### 2. Create service accounts

**URL:** `https://console.cloud.google.com/iam-admin/serviceaccounts?project=YOUR_PROJECT`

Create two service accounts:

| Name | ID | Purpose |
|---|---|---|
| VentureBot Runtime | `vb-runtime` | Cloud Run service identity |
| VentureBot Deployer | `vb-deploy` | GitHub Actions CI/CD |

### 3. Grant IAM roles

**URL:** `https://console.cloud.google.com/iam-admin/iam?project=YOUR_PROJECT`

#### vb-runtime needs:

| Role | Why |
|---|---|
| `Secret Manager Secret Accessor` | Read GOOGLE_API_KEY and GOOGLE_CLIENT_SECRET |
| `Storage Object Admin` | Read/write data snapshots from GCS |

#### vb-deploy needs (all of these — discovered through trial and error):

| Role | Why |
|---|---|
| `Cloud Run Admin` | Deploy/update Cloud Run service |
| `Service Account User` | Set vb-runtime as the service's identity |
| `Artifact Registry Writer` | Push Docker images |
| `Cloud Build Editor` | Run `gcloud builds submit` |
| `Storage Admin` | Access Cloud Build bucket + data bucket |
| `Service Usage Consumer` | Verify APIs are enabled |
| `Secret Manager Secret Accessor` | Pass secrets to Cloud Run via `--set-secrets` |
| `Viewer` | Read project metadata (project number, service URL) |

**How to grant:** Click ✏️ on each service account row → **+ ADD ANOTHER ROLE** → search for each role → **SAVE**

### 4. Create Artifact Registry

**URL:** `https://console.cloud.google.com/artifacts?project=YOUR_PROJECT`

- **+ CREATE REPOSITORY**
- Name: `venturebot`
- Format: `Docker`
- Region: same as your Cloud Run region
- Click **CREATE**

### 5. Create secrets

**URL:** `https://console.cloud.google.com/security/secret-manager?project=YOUR_PROJECT`

Create two secrets (click **+ CREATE SECRET** twice):

| Name | Value |
|---|---|
| `GOOGLE_API_KEY` | Your Gemini API key |
| `GOOGLE_CLIENT_SECRET` | Your Google OAuth client secret |

### 6. Create GCS bucket

**URL:** `https://console.cloud.google.com/storage/browser?project=YOUR_PROJECT`

- **+ CREATE BUCKET**
- Name: `{project-id}-venturebot-data`
- Location: same region as Cloud Run
- Click **CREATE**

Then grant `vb-runtime` access:
- Click the bucket → **PERMISSIONS** tab → **+ GRANT ACCESS**
- Principal: `vb-runtime@YOUR_PROJECT.iam.gserviceaccount.com`
- Role: `Storage Object Admin`
- Click **SAVE**

### 7. Set up Workload Identity Federation

**URL:** `https://console.cloud.google.com/iam-admin/workload-identity-pools?project=YOUR_PROJECT`

**Create the pool:**
- **+ CREATE POOL**
- Name: `github-pool`
- Click **CONTINUE**

**Add a provider:**
- **+ ADD PROVIDER**
- Provider name: `github-provider`
- Issuer URL: `https://token.actions.githubusercontent.com`
- Click **CONTINUE**
- Attribute mapping: set `google.subject` = `assertion.sub` and `attribute.repository` = `assertion.repository`
- Attribute condition: `assertion.repository=='tdk67/venturebot'`
- Click **CONTINUE** → **SAVE**

**Grant access (bind to vb-deploy):**
- Back on the pool page, click **GRANT ACCESS** (or "Connected service accounts" tab)
- Service account: `vb-deploy@YOUR_PROJECT.iam.gserviceaccount.com`
- Attribute: `attribute.repository/tdk67/venturebot`
- Click **SAVE**

### 8. Configure GitHub repo

**URL:** `https://github.com/tdk67/venturebot/settings/variables/actions`

Add these **Actions variables**:

| Name | Value | How to find |
|---|---|---|
| `GCP_PROJECT` | `venturebot-506408` | Your project ID |
| `GCP_REGION` | `europe-west3` | Your region |
| `GCP_WIF_PROVIDER` | `projects/442488405067/locations/global/workloadIdentityPools/github-pool/providers/github-provider` | WIF pool page → provider details |
| `GCP_DEPLOY_SA` | `vb-deploy@venturebot-506408.iam.gserviceaccount.com` | Service account email |
| `GCP_DATA_BUCKET` | `venturebot-506408-venturebot-data` | Your GCS bucket name |

### 9. Deploy & OAuth

1. Push to `main` → GitHub Actions deploys automatically
2. Get the service URL: `gcloud run services describe venturebot --region europe-west3 --format='value(status.url)'`
3. Add `<URL>/api/auth/callback` to your Google OAuth client's **Authorized redirect URIs**
4. Set env vars on the service:
   ```bash
   gcloud run services update venturebot --region europe-west3 \
     --update-env-vars="VENTUREBOT_PUBLIC_BASE_URL=<URL>,GOOGLE_CLIENT_ID=<your-client-id>"
   ```

---

## The credential model (why things failed)

There are **three identities** in play:

| Identity | Who | What it can do |
|---|---|---|
| **Your Google account** | You (owner) | Everything — enable APIs, grant IAM, create resources |
| **vb-deploy** | GitHub Actions CI/CD | Deploy to Cloud Run, push images, read secrets, trigger builds |
| **vb-runtime** | Cloud Run service itself | Read secrets at startup, read/write GCS data snapshots |

**The chicken-and-egg problem we hit:**

1. Cloud Shell is rate-limited and can kick you out mid-session
2. The `vb-deploy` service account key (`gcp-credentials.json`) can't bootstrap itself — it can't grant IAM roles to itself
3. The Cloud Resource Manager API was disabled, which blocked all IAM operations
4. Manual UI clicking was needed to: enable the API + grant the first set of roles

**How the setup script avoids this:**

- Runs on your **local machine** with your **owner Google account** (not Cloud Shell, not a service account key)
- No rate limiting, no session timeout
- Your owner account has full permissions, so everything just works
- Fully idempotent — no chicken-and-egg problems

**The `.gcp-credentials.json` file:**

This is a service account key for `vb-deploy`. It's useful for:
- GitHub Actions (via WIF, not this key — the key is a backup)
- Debugging from the VPS (limited to what vb-deploy can do)

It is NOT useful for:
- Setting up the project (lacks IAM permissions)
- Granting roles (can't grant to itself)
- Enabling APIs (no `serviceusage.admin`)

---

## Troubleshooting

### "Cloud Resource Manager API has not been used"

**Fix:** Open `https://console.cloud.google.com/apis/api/cloudresourcemanager.googleapis.com/overview?project=YOUR_PROJECT` and click **ENABLE**. This must be done by the project owner (your Google account), not a service account.

### "PERMISSION_DENIED" on Cloud Build

**Fix:** `vb-deploy` needs `roles/cloudbuild.builds.editor` and `roles/storage.admin`. Both are in the setup script and correct role list above.

### "Permission 'secretmanager.secrets.create' denied"

**Fix:** `vb-deploy` needs `roles/secretmanager.secretAccessor` (to read and pass secrets to Cloud Run). It does NOT need `roles/secretmanager.admin` — the setup script creates secrets using your owner account.

### "Permission 'artifactregistry.repositories.create' denied"

**Fix:** Either create the Artifact Registry manually (one-time), or grant `vb-deploy` `roles/artifactregistry.admin` temporarily. The setup script creates it using your owner account.

### Google login not working on deployed app

Check these three things:
1. `VENTUREBOT_PUBLIC_BASE_URL` is set to the Cloud Run URL (or custom domain)
2. `GOOGLE_CLIENT_ID` is set
3. The redirect URI (`<URL>/api/auth/callback`) is added to your Google OAuth client

### Cloud Shell is rate-limited

**Fix:** Don't use Cloud Shell. Install `gcloud` locally and run the setup script. Cloud Shell has weekly hour limits and idle timeouts.

---

## Current State (venturebot-506408)

| Resource | Status | Details |
|---|---|---|
| APIs | ✅ Enabled | 7 APIs |
| vb-runtime SA | ✅ Created | `roles/secretmanager.secretAccessor`, `roles/storage.objectAdmin` |
| vb-deploy SA | ✅ Created | 8 roles (see list above) |
| Artifact Registry | ✅ Created | `venturebot` in `europe-west3` |
| Secrets | ✅ Created | `GOOGLE_API_KEY`, `GOOGLE_CLIENT_SECRET` |
| GCS bucket | ✅ Created | `venturebot-506408-venturebot-data` |
| WIF | ✅ Configured | `github-pool/github-provider` → `vb-deploy` |
| Cloud Run | ✅ Deployed | `https://venturebot-442488405067.europe-west3.run.app` |
| Google OAuth | ✅ Working | Redirect URI configured |
| GitHub Actions | ✅ Configured | Variables set, pipeline runs on push to main |