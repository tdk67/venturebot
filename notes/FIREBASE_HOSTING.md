# Firebase Hosting → Cloud Run custom domain (venture-bot)

Cloud Run's built-in `domain-mappings` are **not supported in `europe-west3`**.
This uses Firebase Hosting as a free, GA, Google-native CDN in front of the
Cloud Run service. Firebase rewrites the custom domain to Cloud Run, and
`europe-west3` (Frankfurt) **is** on Firebase's supported rewrite-region list.

**Target:** `https://venture-bot.taskmind-ai.com` → `venturebot` (Cloud Run, europe-west3)

---

## What's already in the repo (committed)

| File | Purpose |
|---|---|
| `firebase.json` | Hosting config: `rewrites` `**` → `run` `venturebot` in `europe-west3`, plus `no-store` cache headers |
| `.firebaserc` | Maps the `default` alias → GCP project `venturebot-506408` |
| `public/index.html` | Placeholder (never served — everything rewrites to Cloud Run) |

---

## Step 1 — Create the Firebase project + Hosting site (owner, one-time)

Firebase must be initialized by the **project owner** (your Google account) —
`vb-deploy` can't enable the Firebase API or create the site.

1. Open https://console.firebase.google.com
2. **Add project** → select existing **`venturebot-506408`** → skip Analytics → **Create**.
   (This enables `firebase.googleapis.com` automatically.)
3. **Build → Hosting → Get started**, then create a **Site**.
   - Suggested Site ID: `venturebot` (this is just the Firebase site name; the
     custom domain is added separately below).
4. Optional but recommended: **Build → Hosting → Add custom domain**, type
   `venture-bot.taskmind-ai.com`, and let the console show you the exact
   TXT + A records. (Firebase uses the two Fastly A records below.)

---

## Step 2 — DNS at Hostinger (you)

Remove the old record and add Firebase's A records:

```text
REMOVE:  CNAME  venture-bot  ghs.googlehosted.com     # old Google Sites style

ADD:
  Type  Name         Value
  A     venture-bot  199.36.158.100
  A     venture-bot  199.36.158.101
  TXT   (as shown by the Firebase console, to prove ownership)
```

Firebase cannot provision the SSL cert if any stale `CNAME`/`AAAA` records
still point elsewhere, so delete them fully.

---

## Step 3 — Deploy the hosting rewrite (owner)

```bash
firebase login                 # your owner Google account
cd /root/venturebot
firebase deploy --only hosting
```

This is a **one-time** action. `firebase.json` rewrites `**` → the Cloud Run
service by name, so every future `gcloud run deploy` (via GitHub Actions) is
automatically served under the custom domain — no Firebase redeploy needed.

---

## Step 4 — Point the app at the new domain

```bash
gcloud run services update venturebot --region=europe-west3 \
  --update-env-vars="VENTUREBOT_PUBLIC_BASE_URL=https://venture-bot.taskmind-ai.com,GOOGLE_CLIENT_ID=<your-client-id>"
```

And add the OAuth redirect URI:
`https://venture-bot.taskmind-ai.com/api/auth/callback`

Then set the GitHub Actions **variables** so future deploys keep it:
- `PUBLIC_BASE_URL = https://venture-bot.taskmind-ai.com`
- `GOOGLE_CLIENT_ID = <your-client-id>`

---

## Notes / gotchas

- **SSE (live debate feed) works through Hosting.** `firebase-tools` streams
  `text/event-stream` responses unbuffered (see `lib/hosting/cloudRunProxy.js`),
  and the app pings every 15 s, so the EventSource stays alive.
- **60 s request timeout** is Hosting's only hard limit. It does not apply to
  the long-lived EventSource stream (keepalive-padded); normal API calls are
  far under 60 s.
- **`no-store` headers** in `firebase.json` keep the dynamic UI from being
  cached by the CDN.
