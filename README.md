# Bioinformatics Platform

A full-stack, visual bioinformatics pipeline execution platform built for the Turkish market. Drag-and-drop a pipeline on a canvas, upload your genomic data, pay per run, and get results — all in the browser. Designed for KVKK compliance and hosted on Turkish cloud infrastructure.

---

## What It Does

- **Visual pipeline builder** — drag nodes onto a canvas, connect them, and run
- **nf-core/sarek** — variant calling (GATK HaplotypeCaller, DeepVariant, Strelka2, FreeBayes) — primary MVP workflow
- **nf-core pipelines** — rnaseq, atacseq, methylseq, ampliseq, chipseq, fetchngs
- **Snakemake workflows** — 4700+ community workflows + 454 wrappers
- **BioScript** — custom bash script runs inside a Docker image pre-loaded with bio tools
- **Custom Linux pipelines** — SPAdes, Kraken2, Prokka, IQ-TREE 2, Flye (de novo, metagenomics, annotation, phylogenomics, long-read assembly)
- **Mutation Assessment** — post-sarek pipeline: annotates VCF variants against 17 public databases and generates a PDF report
- **CNV/SV analysis** — structural variant parsing from VCF (DEL, DUP, INV, INS, BND, CNV, TRA)
- **Paired-end FASTQ** — upload R1 + R2 and both get passed to the runner
- **Pay-per-run billing** — Stripe (global) or iyzico (Turkey) checkout, cost estimated before every job
- **Live results** — volcano plots, VCF tables, MultiQC HTML, file lists — auto-detected

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18 + TypeScript + Vite, @xyflow/react v12 |
| Backend | FastAPI + Uvicorn (async) |
| Job queue | Celery 5 + Redis 7 |
| Database | PostgreSQL 16 + SQLAlchemy 2 + Alembic |
| Auth | JWT + RBAC (roles) + MFA/TOTP (pyotp) |
| Payments | Stripe Checkout (global) + iyzico (Turkey) |
| Cloud runners | Turkish cloud VMs (Huawei / Turkcell / CloudSigma) or AWS Batch |
| Storage | Local filesystem (dev), AWS S3, or Turkish S3-compatible (Huawei OBS / Turkcell nDepo) |
| Observability | Sentry (error tracking) + Prometheus `/metrics` |
| Containers | Docker + Docker Compose |

---

## Running Locally

### Prerequisites

- Docker Desktop (or Docker Engine + Docker Compose plugin)
- 8 GB RAM available for Docker (4 GB minimum)

### Start (demo / debug mode — no cloud credentials needed)

```bash
git clone <your-repo-url>
cd bioinformatics_platform
docker compose up
```

Wait for:

```
backend-1  | INFO:     Application startup complete.
frontend-1 | ➜  Local:   http://localhost:5173/
```

| Service | URL |
|---------|-----|
| App | http://localhost:5173 |
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Prometheus metrics | http://localhost:8000/metrics |

Register an account on first visit. Everything works out of the box:

- **Storage** → files saved to a local Docker volume (`/uploads`)
- **sarek / nf-core** → mock runner returns realistic fake results in ~10 s
- **Snakemake / BioScript / Custom** → mock runners, no tools installed
- **Assessment pipeline** → fully real (queries ClinVar, gnomAD, CADD, etc. live)
- **Payments** → Stripe and iyzico are optional; jobs can be created directly via the API

### Running modes

There are four modes for every runner, controlled by `NEXTFLOW_BACKEND`, `SNAKEMAKE_BACKEND`, and `BIOSCRIPT_BACKEND`:

| Mode | Value | What happens | When to use |
|------|-------|-------------|-------------|
| **Mock** (default) | `mock` | Returns realistic fake data in ~10 s | Demos, UI development |
| **Local** | `local` | Runs real pipeline via Docker on your machine | Testing locally |
| **Turkish cloud** | `turkishcloud` | Spins up an ephemeral VM on Huawei/Turkcell/CloudSigma | Production (Turkey) |
| **AWS Batch** | `awsbatch` | Submits to AWS Batch | Production (AWS) |

```bash
# Demo mode (default — no setup needed)
docker compose up

# Turkish cloud production
NEXTFLOW_BACKEND=turkishcloud SNAKEMAKE_BACKEND=turkishcloud docker compose up

# AWS Batch production
NEXTFLOW_BACKEND=awsbatch SNAKEMAKE_BACKEND=awsbatch docker compose up
```

### Stop

```bash
docker compose down          # keep database volumes
docker compose down -v       # full reset (wipes all data)
```

### Useful commands

```bash
docker compose logs -f
docker compose logs -f worker
docker compose exec frontend sh -c "cd /app && npx tsc --noEmit"
docker compose exec backend bash
docker compose exec backend pytest tests/ -v
docker compose restart worker
```

### Troubleshooting

| Problem | Fix |
|---------|-----|
| Port 5432 in use | Stop local Postgres or change `5432:5432` in docker-compose.yml |
| Port 6380 in use | Change `6380:6379` to another port |
| Assessment job fails with network errors | The assessment runner calls live public APIs — check internet connection |
| Jobs stuck in `pending` | Worker crashed — run `docker compose logs worker` |
| Turkish cloud VM never completes | Check `completion/{job_id}/done` exists in object storage; verify provider credentials |

---

## Project Structure

```
bioinformatics_platform/
├── backend/
│   ├── alembic/versions/          # DB migrations (0001 – 0014)
│   ├── app/
│   │   ├── api/v1/                # REST routers: auth, uploads, jobs,
│   │   │   │                      #   pipelines, nfcore, snakemake,
│   │   │   │                      #   payments, iyzico
│   │   ├── models/                # User, Job, Pipeline, NfCorePipeline,
│   │   │   │                      #   NfCoreModule, SnakemakeWrapper,
│   │   │   │                      #   SnakemakeWorkflow, AuditLog,
│   │   │   │                      #   ConsentRecord
│   │   ├── schemas/               # Pydantic request / response models
│   │   ├── services/
│   │   │   ├── storage/           # local.py + s3.py (S3-endpoint-aware)
│   │   │   ├── nextflow/          # mock, local, batch (AWS), turkishcloud
│   │   │   ├── snakemake/         # mock, local, batch (AWS), turkishcloud
│   │   │   ├── bioscript/         # mock, local, batch (AWS), turkishcloud
│   │   │   ├── vm_provisioner/    # base, huawei, turkcell, cloudsigma,
│   │   │   │   │                  #   factory (fallback), runner (shared)
│   │   │   ├── assessment/        # real.py + databases.py + report.py
│   │   │   ├── sv_parser.py       # structural variant VCF parser
│   │   │   ├── audit.py           # fire-and-forget audit log writer
│   │   │   └── auth.py            # JWT creation/verification + MFA tokens
│   │   ├── tasks/                 # Celery tasks: pipeline, scrape_*
│   │   ├── config.py              # All env vars (Pydantic Settings)
│   │   └── main.py                # Sentry init + Prometheus + health check
│   ├── Dockerfile
│   ├── Dockerfile.worker          # Java 17 + Nextflow + Snakemake
│   ├── Dockerfile.tools           # samtools, bwa, STAR, Kraken2, SPAdes…
│   ├── bioplatform_helpers.sh     # Shell library for BioScript jobs
│   ├── nextflow_aws.config
│   ├── .env.example               # All env vars documented
│   └── requirements.txt
│
├── frontend/
│   └── src/
│       ├── api/
│       ├── builder/               # Canvas, nodes, validation, templates,
│       │   │                      #   Spotlight, TemplateGallery, undo/redo
│       ├── components/            # AuthGate, TierConfirm, JobProgress,
│       │   │                      #   JobHistory, ResultsPanel, ResultViewer
│       └── App.tsx
│
├── terraform/                     # AWS infra (S3, IAM, Batch, ECR)
├── .github/workflows/             # CI (ruff+mypy+pytest+tsc) + CD (ECR+S3)
├── .env.example
└── docker-compose.yml
```

---

## Turkish Cloud Infrastructure

The platform is designed to run compute jobs on Turkish cloud providers to satisfy KVKK data residency requirements for health data. Compute is **ephemeral and per-job** — a VM is created when a job starts and terminated when it finishes. Customers pay only for what they use.

### Provider stack

| Priority | Provider | Location | API | Storage |
|----------|----------|----------|-----|---------|
| 1 | **Huawei Cloud** | Istanbul (tr-west-1) | `huaweicloudsdkecs` | Huawei OBS (S3-compatible) |
| 2 | **Turkcell Bulut** | Istanbul / Ankara / İzmir | VMware vCD (`pyvcloud`) | Turkcell nDepo (S3-compatible) |
| 3 | **CloudSigma / Siaflex** | İzmir | REST API (no SDK) | Uses whichever OBS/nDepo is configured |

### How fallback works

On each job submission, `COMPUTE_PROVIDERS` (default: `huawei,turkcell,cloudsigma`) is tried in order. The first provider that passes a health check **and** successfully creates an instance is used. If Huawei's API is down or quota is exhausted, Turkcell is tried automatically; then CloudSigma. The pipeline task never needs to know which provider ran the job.

### VM lifecycle

```
Job submitted
    │
    ▼
factory.get_provisioner_with_fallback()
    │  tries huawei → turkcell → cloudsigma
    ▼
VM created with cloud-init user_data script
    │
    ▼  (VM runs independently)
    │  1. Installs Docker + AWS CLI
    │  2. Pulls bioplatform/worker image
    │  3. Runs pipeline (Nextflow / Snakemake / BioScript)
    │  4. Uploads results to object storage
    │  5. Writes completion/{job_id}/done or /error marker
    │  6. shutdown -h now
    ▼
Celery task polls object storage every 30 s (max 3 h 45 m)
    │
    ▼
Results collected → VM terminated (always, even on failure)
```

### Storage for Turkish cloud

Both Huawei OBS and Turkcell nDepo expose an S3-compatible API. Set `S3_ENDPOINT_URL` to switch the storage backend:

```bash
# Huawei OBS (Turkey)
S3_ENDPOINT_URL=https://obs.tr-west-1.myhuaweicloud.com
AWS_ACCESS_KEY_ID=<huawei_ak>
AWS_SECRET_ACCESS_KEY=<huawei_sk>
S3_BUCKET=<obs_bucket_name>

# Turkcell nDepo
S3_ENDPOINT_URL=<ndep0_endpoint_provided_by_turkcell>
AWS_ACCESS_KEY_ID=<ndep0_access_key>
AWS_SECRET_ACCESS_KEY=<ndep0_secret_key>
```

The existing `STORAGE_BACKEND=s3` setting and all download/upload logic works unchanged.

### VM flavors

| Flavor | Huawei ECS | Turkcell vCD | CloudSigma |
|--------|-----------|-------------|-----------|
| `small` | c7n.large.4 (2 vCPU / 8 GB) | 2 vCPU / 8 GB | 4 GHz / 8 GB |
| `standard` | c7n.2xlarge.4 (8 vCPU / 32 GB) | 8 vCPU / 32 GB | 16 GHz / 32 GB |
| `large` | c7n.4xlarge.4 (16 vCPU / 64 GB) | 16 vCPU / 64 GB | 32 GHz / 64 GB |
| `xlarge` | m7n.4xlarge.8 (16 vCPU / 128 GB) | 16 vCPU / 128 GB | 48 GHz / 128 GB |

Set `DEFAULT_VM_FLAVOR=standard` (default) or pass `tier` in `workflow_config` per job.

---

## Security & Compliance

### Authentication

- JWT bearer tokens (7-day expiry by default, configurable)
- RBAC: `role` field on every user — `user`, `clinician`, `admin`
- MFA/TOTP via `pyotp` — compatible with any authenticator app (Google Authenticator, Authy, etc.)
  - `POST /auth/mfa/setup` → provisioning URI + QR data
  - `POST /auth/mfa/verify` → activate MFA with a valid code
  - `POST /auth/mfa/complete` → exchange MFA token + code for a full JWT (called at login challenge screen)
  - `DELETE /auth/mfa` → disable
- Login with MFA enabled returns `mfa_required: true` + a short-lived (5 min) `mfa_token` instead of a full JWT

### RBAC

```python
from app.api.v1.deps import require_role

@router.delete("/admin/something")
async def admin_only(user = Depends(require_role("admin"))):
    ...

@router.post("/clinical/report")
async def clinician_or_admin(user = Depends(require_role("clinician", "admin"))):
    ...
```

### Audit log

Every authentication event, job creation, cancellation, retry, and consent change is written to an append-only `audit_log` table. Writes are fire-and-forget (own DB session, never blocks the request). Logged fields: `user_id`, `action`, `resource_type`, `resource_id`, `ip_address`, `user_agent`, `meta` (JSON), `created_at`.

### KVKK compliance

Turkey's personal data protection law (KVKK) applies to all health data processed on this platform.

- **Consent records** — `POST /auth/consent` records explicit KVKK consent per user per consent type (e.g. `"kvkk"`, `"marketing"`). Upsert semantics; full audit trail.
- **Data residency** — `data_residency` field on User (default `"TR"`). Turkish cloud infrastructure keeps compute and storage physically in Turkey.
- **Right to erasure** — `DELETE /auth/me` deletes the user, all their jobs, and queues S3 object deletion (KVKK Article 7 + GDPR Article 17).
- **VERBİS** — Register your data processing activities at [verbis.kvkk.gov.tr](https://verbis.kvkk.gov.tr) before going live. Health data is a special category under KVKK Article 6 — explicit consent is required.

### BioScript sandboxing

User bash scripts run inside Docker with hard resource limits:

```
--memory=8g
--cpus=$(nproc)
--read-only
--tmpfs /tmp:exec
```

In local runner mode, OS-level limits are also applied via `resource.setrlimit`:
- CPU: 2 hours max
- Virtual memory: 8 GB
- File size: 10 GB
- Processes: 256

---

## Payments

### Stripe (global)

Standard Stripe Checkout flow. Set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`.

### iyzico (Turkey)

iyzico is a Turkish payment gateway widely used by Turkish businesses. The flow:

1. `POST /payments/iyzico/checkout` — creates a CheckoutFormInitialize and returns the iyzico HTML form snippet + `conversation_token`
2. Frontend embeds the form; customer completes payment
3. iyzico POSTs to `POST /payments/iyzico/callback` — signature verified, Job created, `job_id` stored in Redis
4. Frontend polls `GET /payments/iyzico/session/{token}` to retrieve the `job_id`

Required env vars: `IYZICO_API_KEY`, `IYZICO_SECRET_KEY`. Sandbox URL: `https://sandbox.iyzipay.com` (default). Production: `https://api.iyzipay.com`.

---

## Pipeline Runners

| `pipeline_id` | Runner | Backend env var | Notes |
|---------------|--------|----------------|-------|
| `"sarek"` | Nextflow | `NEXTFLOW_BACKEND` | nf-core variant calling; auto-generates samplesheet |
| other nf-core | Nextflow | `NEXTFLOW_BACKEND` | rnaseq, atacseq, etc. |
| `"snakemake"` | Snakemake | `SNAKEMAKE_BACKEND` | Generates Snakefile from canvas wrappers/workflows |
| `"bioscript"` | BioScript | `BIOSCRIPT_BACKEND` | Runs user's bash script in tools Docker image |
| `"custom-*"` | Custom | `CUSTOM_BACKEND` | spades, kraken2, prokka, iqtree, flye |
| `"assessment"` | Assessment | always real | Annotates VCF against 17 databases; generates PDF |

Each runner supports: `mock` → `local` → `turkishcloud` → `awsbatch`

---

## Mutation Assessment Pipeline

The Assessment pipeline takes a completed sarek job's VCF output and annotates every variant against 17 public databases.

### How to use

1. Run a sarek job (or select a completed one).
2. Drop an **Assessment** node on the canvas and connect it to the sarek Results node.
3. Pick the source job from the dropdown in the Assessment node.
4. Submit — no file upload needed.
5. Results: interactive variant table + downloadable PDF report.

### PDF report contents

- Summary stats — total variants, pathogenic/LP count, cancer hotspot count
- Classification chart — bar chart by ACMG bucket
- **Table A** — ClinVar significance, InterVar/ACMG classification + criteria, gnomAD AF, popmax AF, hotspot flag, rsID
- **Table B** — SIFT, PolyPhen-2, CADD phred, REVEL, MetaLR, MetaSVM, MutationTaster, SpliceAI Δmax, GERP++, PhyloP
- **Table C** — protein name + function (UniProt), OMIM disease, ClinGen validity, GenCC, Orphanet diseases, HPO terms, LOVD variant count

### Variant-level databases (queried per variant, all free)

| # | Database | What it provides |
|---|----------|-----------------|
| 1 | ClinVar | Pathogenicity classification, HGVS notation |
| 2 | gnomAD v4.1 | Population allele frequency, popmax AF, AC/AN |
| 3 | Ensembl VEP | SIFT, PolyPhen-2, consequence terms, canonical transcript |
| 4 | CADD v1.7 | Phred-scaled deleteriousness score |
| 5 | MyVariant.info | REVEL, MetaLR, MetaSVM, MutationTaster, GERP++, PhyloP |
| 6 | SpliceAI | Splice site disruption Δ scores |
| 7 | InterVar | ACMG/AMP 2015 auto-classification + criteria met |
| 8 | CancerHotspots.org | Recurrent cancer driver mutation hotspot flag |
| 9 | dbSNP | rsID fallback |

### Gene-level databases (queried once per gene, cached 7 days)

| # | Database | What it provides |
|---|----------|-----------------|
| 10 | UniProt | Protein name + function |
| 11 | HGNC | Authoritative gene symbol, Entrez/Ensembl IDs |
| 12 | ClinGen | Gene-disease validity classification |
| 13 | GenCC | Aggregated gene-disease classifications |
| 14 | HPO / Ensembl | Phenotype terms |
| 15 | LOVD | Locus-specific variant count |

### Optional (free registration required)

| # | Database | How to enable |
|---|----------|---------------|
| 16 | OMIM | Set `OMIM_API_KEY` — register at [omim.org/api](https://www.omim.org/api) |
| 17 | Orphanet | Set `ORPHANET_API_KEY` — register at [orphacode.org](https://api.orphacode.org) |

---

## API Overview

All endpoints under `/api/v1`. JWT required in `Authorization: Bearer <token>` except auth and webhooks.

```
# Auth
POST   /auth/register                 Register
POST   /auth/login                    Login → JWT (or mfa_required + mfa_token)
GET    /auth/me                       Current user
DELETE /auth/me                       Delete account (KVKK right to erasure)
POST   /auth/mfa/setup                Generate TOTP secret + provisioning URI
POST   /auth/mfa/verify               Activate MFA with first valid code
POST   /auth/mfa/complete             Exchange mfa_token + code → full JWT
DELETE /auth/mfa                      Disable MFA
POST   /auth/consent                  Record KVKK/GDPR consent
GET    /auth/consent                  List consent records

# Uploads
POST   /uploads/presign               Presigned upload URL + cost estimate
GET    /uploads/estimate              Cost estimate
GET    /uploads/local/{filename}      Serve local file (PDF reports etc.)

# Jobs
GET    /jobs                          List jobs (last 50)
POST   /jobs                          Create + dispatch job
GET    /jobs/{id}                     Job details + result
DELETE /jobs/{id}                     Cancel job
POST   /jobs/{id}/retry               Retry failed/cancelled job
GET    /jobs/{id}/logs?offset=N       Stream log lines
GET    /jobs/{id}/download?path=…     Presigned S3 download URL
GET    /jobs/{id}/vcf                 Paginated VCF variant table
GET    /jobs/{id}/sv                  Structural variant / CNV records

# Pipelines
GET    /pipelines                     List saved pipeline graphs
POST   /pipelines                     Save pipeline graph
GET/PUT/DELETE /pipelines/{id}

# Catalogs
GET    /nfcore/pipelines              nf-core pipeline catalog
GET    /nfcore/modules                nf-core module catalog
POST   /nfcore/refresh
GET    /snakemake/wrappers
GET    /snakemake/workflows
POST   /snakemake/refresh

# Payments
POST   /payments/checkout             Stripe checkout session
POST   /payments/webhook              Stripe webhook
GET    /payments/session/{id}         Poll for job_id after Stripe redirect
POST   /payments/iyzico/checkout      iyzico CheckoutFormInitialize
POST   /payments/iyzico/callback      iyzico result callback
GET    /payments/iyzico/session/{token} Poll for job_id after iyzico payment

# System
GET    /health                        DB + Redis connectivity check
GET    /metrics                       Prometheus metrics (text/plain)
```

---

## Environment Variables

Copy `backend/.env.example` to `.env`. Key groups:

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_SECRET` | `changeme-…` | **Change in production.** Min 32 random chars. |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | Comma-separated CORS origins |
| `MFA_ISSUER` | `BioplatformMD` | Issuer name shown in authenticator apps |
| `DEBUG` | `true` | Set `false` in production (enforces JWT_SECRET check) |

### Runners

| Variable | Default | Options |
|----------|---------|---------|
| `NEXTFLOW_BACKEND` | `mock` | `mock`, `local`, `awsbatch`, `turkishcloud` |
| `SNAKEMAKE_BACKEND` | `mock` | `mock`, `local`, `awsbatch`, `turkishcloud` |
| `BIOSCRIPT_BACKEND` | `mock` | `mock`, `local`, `awsbatch`, `turkishcloud` |

### Turkish cloud compute

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPUTE_PROVIDERS` | `huawei,turkcell,cloudsigma` | Priority-ordered fallback list |
| `DEFAULT_VM_FLAVOR` | `standard` | `small`, `standard`, `large`, `xlarge` |
| `S3_ENDPOINT_URL` | `` | Huawei OBS or Turkcell nDepo endpoint |
| `HUAWEI_AK` | `` | Huawei Cloud Access Key |
| `HUAWEI_SK` | `` | Huawei Cloud Secret Key |
| `HUAWEI_PROJECT_ID` | `` | Huawei IAM project ID |
| `HUAWEI_REGION` | `tr-west-1` | Huawei region |
| `HUAWEI_VPC_ID` | `` | VPC for VM instances |
| `HUAWEI_SUBNET_ID` | `` | Subnet for VM instances |
| `HUAWEI_SECURITY_GROUP_ID` | `` | Security group |
| `HUAWEI_IMAGE_ID` | `` | Ubuntu 22.04 base image ID in tr-west-1 |
| `HUAWEI_FLAVOR_DEFAULT` | `c7n.2xlarge.4` | Default ECS flavor |
| `HUAWEI_OBS_ENDPOINT` | `https://obs.tr-west-1.myhuaweicloud.com` | OBS storage endpoint |
| `TURKCELL_VCD_URL` | `https://svm.turkcellbulut.com` | vCloud Director URL |
| `TURKCELL_VCD_ORG` | `` | vCD organisation name |
| `TURKCELL_VCD_VDC` | `` | Virtual Datacenter name |
| `TURKCELL_VCD_USER` | `` | vCD username |
| `TURKCELL_VCD_PASSWORD` | `` | vCD password |
| `TURKCELL_VCD_NETWORK` | `` | Org network name |
| `TURKCELL_VCD_CATALOG` | `` | Catalog containing VM template |
| `TURKCELL_VCD_TEMPLATE` | `` | VM template name (Ubuntu 22.04) |
| `TURKCELL_NDEP0_ENDPOINT` | `` | nDepo S3-compatible storage endpoint |
| `TURKCELL_NDEP0_ACCESS_KEY` | `` | nDepo access key |
| `TURKCELL_NDEP0_SECRET_KEY` | `` | nDepo secret key |
| `CLOUDSIGMA_API_ENDPOINT` | `https://siaflex.cloud/api/2.0` | CloudSigma/Siaflex API |
| `CLOUDSIGMA_USERNAME` | `` | CloudSigma email |
| `CLOUDSIGMA_PASSWORD` | `` | CloudSigma password |

### Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `STORAGE_BACKEND` | `local` | `local` or `s3` |
| `S3_ENDPOINT_URL` | `` | Override for Turkish-cloud S3-compatible storage |
| `AWS_ACCESS_KEY_ID` | `` | AK for S3 / Huawei OBS / Turkcell nDepo |
| `AWS_SECRET_ACCESS_KEY` | `` | SK |
| `S3_BUCKET` | `` | Bucket name |

### Payments

| Variable | Default | Description |
|----------|---------|-------------|
| `STRIPE_SECRET_KEY` | `` | `sk_test_…` or `sk_live_…` |
| `STRIPE_WEBHOOK_SECRET` | `` | `whsec_…` |
| `IYZICO_API_KEY` | `` | iyzico merchant API key |
| `IYZICO_SECRET_KEY` | `` | iyzico merchant secret |
| `IYZICO_BASE_URL` | `https://sandbox.iyzipay.com` | Use `https://api.iyzipay.com` in production |
| `IYZICO_USD_TO_TRY_RATE` | `33.0` | USD→TRY conversion rate |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `SENTRY_DSN` | `` | Leave empty to disable. Get from sentry.io |

### Mutation Assessment

| Variable | Default | Description |
|----------|---------|-------------|
| `ASSESSMENT_GENOME` | `hg38` | `hg19` or `hg38` |
| `OMIM_API_KEY` | `` | Optional — [omim.org/api](https://www.omim.org/api) |
| `ORPHANET_API_KEY` | `` | Optional — [orphacode.org](https://api.orphacode.org) |

---

## BioScript Shell Helpers

Pre-loaded functions inside the BioScript container:

```bash
bioplatform_qc          <input.fastq.gz> <outdir> [r2.fastq.gz]
bioplatform_align       <reads.fastq.gz> <genome.fa> <outdir> [r2]
bioplatform_star_align  <reads.fastq.gz> <star_index_dir> <outdir> [r2]
bioplatform_call        <input.bam> <genome.fa> <outdir>
bioplatform_featurecount <bam> <gtf> <outdir>
bioplatform_multiqc     <results_dir> <outdir>
bioplatform_spades      <r1.fastq.gz> <outdir> [r2.fastq.gz]
bioplatform_kraken2     <r1.fastq.gz> <db_dir> <outdir> [r2.fastq.gz]
bioplatform_prokka      <assembly.fasta> <outdir>
bioplatform_iqtree      <alignment.fasta> <outdir>
bioplatform_flye        <reads.fastq.gz> <outdir>
```

Available env vars in every BioScript job:

```bash
$INPUT_FILE    # storage URI of the uploaded input file
$OUTPUT_DIR    # storage prefix where outputs should be written
$JOB_ID        # unique job identifier
```

---

## Database Migrations

Migrations run automatically at startup (`alembic upgrade head`).

| Version | Description |
|---------|-------------|
| 0001 | Create jobs table |
| 0002 | Create pipelines table |
| 0003 | Create nf-core catalog tables |
| 0004 | Add pipeline input formats |
| 0005 | Add pipeline_id to jobs |
| 0006 | Create Snakemake catalog tables |
| 0007 | Create users table; add user_id to jobs + pipelines |
| 0008 | Add stripe_session_id to jobs |
| 0009 | Add storage_key_r2 + workflow_config to jobs |
| 0010 | Add job_name to jobs |
| 0011 | Add role to users (RBAC) |
| 0012 | Create audit_log table |
| 0013 | Add mfa_secret, mfa_enabled, data_residency to users |
| 0014 | Create consent_records table (KVKK) |

---

## License

MIT
