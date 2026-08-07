# Deployment Runbook: GCP e2-micro (Always Free)

Step-by-step for deploying this platform to a single GCP e2-micro Compute Engine VM, with Neo4j on AuraDB Free (managed, replacing the local `docker-compose.yml` Neo4j service) and TLS via Caddy/Let's Encrypt. Follow top to bottom.

**Before you start**, you'll need: a GCP account/project with billing enabled (Always Free doesn't require a paid account, but GCP requires a billing account attached), a domain you can point DNS at (any registrar), and a `GOOGLE_API_KEY` for Gemini.

---

## 1. Create the e2-micro VM

**Region matters for the free tier.** GCP's Always Free e2-micro instance (one per billing account, non-preemptible) is only free in these three regions: `us-west1` (Oregon), `us-central1` (Iowa), `us-east1` (South Carolina). Picking any other region bills you for the instance.

```bash
gcloud compute instances create contract-agent-vm \
  --project=YOUR_PROJECT_ID \
  --zone=us-central1-a \
  --machine-type=e2-micro \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard \
  --tags=http-server,https-server
```

30GB is the Always Free boot disk limit (`pd-standard`) - don't exceed it or the disk itself starts billing.

**Reserve a static external IP** (an ephemeral IP changes if the VM restarts, which breaks DNS):

```bash
gcloud compute addresses create contract-agent-ip --region=us-central1
gcloud compute addresses describe contract-agent-ip --region=us-central1 --format='get(address)'
# Attach it to the running VM:
gcloud compute instances delete-access-config contract-agent-vm --zone=us-central1-a --access-config-name="External NAT"
gcloud compute instances add-access-config contract-agent-vm --zone=us-central1-a --access-config-name="External NAT" --address=$(gcloud compute addresses describe contract-agent-ip --region=us-central1 --format='get(address)')
```

**Open firewall ports 80 and 443** (SSH/22 is already open by default via GCP's default network):

```bash
gcloud compute firewall-rules create allow-http-https \
  --allow=tcp:80,tcp:443 \
  --target-tags=http-server,https-server \
  --direction=INGRESS
```

Do **not** open port 8000 (the backend's direct port, used only for setup/testing in step 9) to the internet - it has no TLS and isn't meant to be public long-term.

---

## 2. Install Docker on the VM

SSH in (`gcloud compute ssh contract-agent-vm --zone=us-central1-a`), then:

```bash
# Docker's official apt repository (not the curl-pipe-to-sh installer)
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Run docker without sudo
sudo usermod -aG docker $USER
# Log out and back in for the group change to take effect
```

**Add a swapfile.** e2-micro has 1GB RAM and no swap by default - a transient spike (a large PDF, a big LLM response) without swap risks the OOM killer taking down a container outright instead of just slowing down:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 3. Provision Neo4j AuraDB Free

At [console.neo4j.io](https://console.neo4j.io), create a free AuraDB instance. Save the generated password immediately - Aura only shows it once. You'll get a connection URI in the form:

```
neo4j+s://xxxxxxxx.databases.neo4j.io
```

No code change is needed for this - `langchain_neo4j.Neo4jGraph` reads `NEO4J_URI`/`NEO4J_USERNAME`/`NEO4J_PASSWORD`/`NEO4J_DATABASE` straight from the environment and is scheme-agnostic (it already works with Aura's `neo4j+s://` TLS scheme). Username is `neo4j` unless you changed it.

**Run the migrations** against your new Aura instance before first use - from your dev machine (with `NEO4J_URI`/`NEO4J_USERNAME`/`NEO4J_PASSWORD` in your environment pointing at Aura):

```bash
cd backend
uv run python migrations/run_all_migrations.py
```

---

## 4. Provision secrets

Two options, not mutually exclusive:

### Option A: plain `.env.production` file (simplest)

Generate real secrets and put them directly in an env file on the VM (not committed anywhere):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # run twice, for JWT_SECRET_KEY and ENCRYPTION_KEY
```

### Option B: GCP Secret Manager for `ENCRYPTION_KEY` specifically

`GCPSecretManagerKeyProvider` (`backend/infrastructure/encryption.py`) fetches the encryption key from Secret Manager instead of a plain env var:

```bash
gcloud services enable secretmanager.googleapis.com
echo -n "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" | \
  gcloud secrets create ENCRYPTION_KEY --data-file=- --replication-policy=automatic

# Grant the VM's service account access
VM_SA=$(gcloud compute instances describe contract-agent-vm --zone=us-central1-a --format='get(serviceAccounts[0].email)')
gcloud secrets add-iam-policy-binding ENCRYPTION_KEY \
  --member="serviceAccount:${VM_SA}" \
  --role="roles/secretmanager.secretAccessor"
```

Then set `KEY_PROVIDER=gcp` and `GCP_PROJECT_ID=YOUR_PROJECT_ID` in your env file instead of a literal `ENCRYPTION_KEY` value - the backend/worker authenticate via the VM's attached service account automatically (Application Default Credentials), no key file involved. `JWT_SECRET_KEY`/`NEO4J_PASSWORD`/`GOOGLE_API_KEY` still come from plain env vars either way (only `ENCRYPTION_KEY` has a native Secret Manager code path) - if you want all of them centrally managed in Secret Manager too, store them the same way and populate your env file from Secret Manager at deploy time instead of hardcoding:

```bash
gcloud secrets versions access latest --secret=JWT_SECRET_KEY
```

### Write `.env.production` on the VM

```bash
cat > ~/.env.production <<'EOF'
NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<your Aura password>
GOOGLE_API_KEY=<your Gemini API key>
JWT_SECRET_KEY=<generated secret>
ENCRYPTION_KEY=<generated secret, or omit if KEY_PROVIDER=gcp>
KEY_PROVIDER=env
CORS_ALLOWED_ORIGINS=https://your-domain.example
DOMAIN=your-domain.example
IMAGE_REGISTRY=us-central1-docker.pkg.dev/YOUR_PROJECT_ID/contract-agent
EOF
chmod 600 ~/.env.production
```

---

## 5. Point DNS at the VM

At your domain registrar, create an **A record** for your domain (or subdomain, e.g. `contracts.your-domain.example`) pointing at the static IP from step 1. Propagation is usually minutes, sometimes longer - Caddy needs this resolvable before it can obtain a Let's Encrypt certificate (step 9 will fail to get TLS, but the stack still works over plain HTTP, if DNS isn't ready yet).

---

## 6. Provision an image registry and build images (not on the VM)

**Build on your dev machine or CI, not on the e2-micro VM itself** - `npm run build`/`uv sync`/`tsc` would badly strain a 1GB, burstable-vCPU box. GCP Artifact Registry is the natural choice here since it authenticates via the same GCP project/service account as everything else:

```bash
gcloud services enable artifactregistry.googleapis.com
gcloud artifacts repositories create contract-agent \
  --repository-format=docker \
  --location=us-central1
```

On your dev machine:

```bash
gcloud auth configure-docker us-central1-docker.pkg.dev

export IMAGE_REGISTRY=us-central1-docker.pkg.dev/YOUR_PROJECT_ID/contract-agent
docker compose -f docker-compose.prod.yml build backend worker ui
docker compose -f docker-compose.prod.yml push backend worker ui
```

(`worker` builds from the same Dockerfile as `backend` and shares its image tag - building it is fast, cached.)

---

## 7. Copy the compose files to the VM

You only need three files on the VM - not the whole repo:

```bash
scp docker-compose.prod.yml Caddyfile contract-agent-vm:~/
```

The VM's service account needs `roles/artifactregistry.reader` to pull (grant it the same way as the Secret Manager binding in step 4, if not already present via a broader role).

---

## 8. Test connectivity before going live

SSH into the VM and do a quick sanity check against Aura and Redis before starting the full stack (this is the "real connectivity test" - confirm your actual `NEO4J_URI`/`NEO4J_PASSWORD` work):

```bash
cd ~
set -a; source .env.production; set +a
gcloud auth configure-docker us-central1-docker.pkg.dev
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d redis backend
sleep 15
curl -s http://localhost:8000/api/monitoring/health
# Expect: {"status":"healthy","components":{"cache":"healthy","neo4j":"healthy",...}}
```

If `neo4j` shows `unhealthy`, double check `NEO4J_URI`/`NEO4J_PASSWORD` and that your Aura instance is fully provisioned (not still spinning up).

---

## 9. Start the full stack

```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
```

Caddy will attempt Let's Encrypt issuance automatically once `DOMAIN` is set and DNS resolves to this VM - watch its log the first time:

```bash
docker compose -f docker-compose.prod.yml logs -f caddy
```

Look for `certificate obtained successfully`. If DNS isn't propagated yet, Caddy retries with backoff - no need to restart anything once DNS catches up.

---

## 10. Verify

- `https://your-domain.example/` - the frontend loads.
- `https://your-domain.example/api/monitoring/health` - through Caddy → `ui` → `backend`, real end-to-end.
- Register an account and log in (`POST /api/auth/register` then the login screen) to confirm the full JWT + Aura + Redis path works.
- `curl -I https://your-domain.example/` - confirm `strict-transport-security` is present (only appears over real HTTPS - see `backend/shared/middleware/security_headers.py`).

Once confirmed working over HTTPS, close port 8000 in the firewall (it was only for step 8's direct test) and remove the `ports: ["8000:8000"]` mapping from `docker-compose.prod.yml` if you want it gone from the container entirely, not just unreachable from outside the VM.

---

## 11. Resource fit (why the numbers in `docker-compose.prod.yml` are what they are)

Measured directly against the real production images on this exact deployment shape (backend + worker + redis + ui + Caddy, no local Neo4j):

| Service | Measured idle RSS | `mem_limit` |
|---|---|---|
| backend | 246 MiB | 300m |
| worker (`--concurrency=1 --pool=solo`) | 236 MiB | 280m |
| redis (`--maxmemory 150mb`) | <10 MiB idle, capped | 160m |
| ui (nginx) | 7 MiB | 40m |
| caddy | 13.5 MiB idle | 64m |

Sum of limits: 844 MiB, leaving ~180MB for the OS + Docker daemon on e2-micro's 1GB. Tight but workable - the swapfile from step 2 is the safety margin, not a substitute for these limits being right-sized. If you see OOM kills in `dmesg` or `docker compose logs`, that's the signal to either raise a specific `mem_limit` (taking the headroom from elsewhere) or reduce load, not to remove the limits.

Two tunings already baked into the compose file for this specific VM size, don't remove them: Celery's `--pool=solo` (the default would fork `os.cpu_count()` full copies of the worker process - e2-micro can't spare that), and Redis's `--maxmemory` cap (unbounded, like the local dev file, isn't safe when sharing 1GB with everything else).

**Search re-ranking does not change this table.** `RERANKING_ENABLED` (default off, `backend` service only) adds no new library or process to either container - it's a real, measured design choice specifically because of the numbers above: a local cross-encoder (`sentence-transformers`) was measured at ~209MB real RSS after one batched inference, which does not fit the ~180MB of remaining headroom this table already treats as a safety margin, not spare capacity. Re-ranking calls Gemini instead, the same external dependency `backend`'s extraction/policy-evaluation calls already use, so it adds real network latency and a small real API cost per search when enabled, not deployed memory. See `docs/CAPSTONE_SUMMARY.md` for the full measurement.

---

## 12. Operating notes

- **Updating**: rebuild + push (step 6) from your dev machine, then on the VM: `docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d`. Celery's `stop_grace_period: 45s` and `task_acks_late=True` mean an in-flight analysis survives the worker container being replaced.
  - **Found live, redeploying the re-ranking work**: recreating `backend` alone (`--force-recreate backend worker`) can leave `ui` (nginx, its own container - `frontend/nginx.conf.template`'s `/api/` proxy) and Caddy holding a stale connection/resolution to the old `backend` container's IP, serving real `502 Bad Gateway` on every route through the public domain even though `backend` itself reports `healthy` and answers directly on `localhost:8000`. Symptom: `curl` against the public HTTPS URL returns 502 with an `nginx/1.27.5` error body (confirms `ui`'s nginx, not Caddy, is the one failing to reach `backend`) while a direct in-VM `curl http://localhost:8000/...` succeeds. Fix: `sudo docker restart contactvishwav-ui-1` (and `contactvishwav-caddy-1` if the same symptom shows one layer earlier) after recreating `backend` - not required every routine deploy if `backend`'s container ID happens not to change, but real and reproducible when it does, so treat it as a standard post-recreate step, not an occasional surprise.
- **Enabling `RERANKING_ENABLED` in production**: deploy with it unset/`false` first (the default) and confirm the rest of the stack is unaffected - it changes nothing about search behavior while off. Enable deliberately by setting `RERANKING_ENABLED=true` in `.env.production` (or directly in the shell before `docker compose up`), then `docker compose -f docker-compose.prod.yml up -d --force-recreate backend` (worker doesn't need recreating - it never reads this var). Watch `docker stats` for `backend` specifically for the first several real searches after enabling - re-ranking adds real Gemini call latency to the search request path, and the circuit breaker/timeout are tuned generically, not against this VM's specific real-world traffic pattern yet.
- **Logs**: `docker compose -f docker-compose.prod.yml logs -f <service>`.
- **Certificate renewal**: automatic - Caddy handles this itself, nothing to schedule.
- **Backups**: Aura Free includes automated backups on Neo4j's side. Redis here is a cache/broker only (LLM response cache, Celery queue, usage counters) - nothing in it needs backing up; losing it just means a cold cache, not data loss.
- **Secrets rotation**: regenerate the secret, update `.env.production` (or the Secret Manager version if using `KEY_PROVIDER=gcp`), then `docker compose -f docker-compose.prod.yml up -d --force-recreate backend worker`.
