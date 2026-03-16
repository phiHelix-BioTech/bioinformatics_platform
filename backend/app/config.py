from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://bioplatform:bioplatform@postgres:5432/bioplatform"

    # Celery
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/0"

    # Backend injection via env vars
    STORAGE_BACKEND: str = "local"       # local | s3
    EC2_BACKEND: str = "mock"            # mock | aws
    NEXTFLOW_BACKEND: str = "mock"       # mock | local | aws
    NEXTFLOW_PROFILE: str = "docker"     # docker | singularity (local mode only)
    SNAKEMAKE_BACKEND: str = "mock"      # mock | aws
    BIOSCRIPT_BACKEND: str = "mock"      # mock | aws
    CUSTOM_BACKEND: str = "mock"         # mock | aws

    # Genome build used for gnomAD / VEP / CADD lookups
    ASSESSMENT_GENOME: str = "hg38"      # hg19 | hg38

    # OMIM — gene-disease relationships (free academic key at omim.org/api)
    OMIM_API_KEY: str = ""

    # Orphanet — rare disease associations (free key at orphacode.org)
    ORPHANET_API_KEY: str = ""

    # Local uploads directory
    UPLOADS_DIR: str = "/uploads"

    # Public base URL for generating upload URLs (used by local backend only)
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    # AWS / S3
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET: str = ""
    S3_PRESIGN_EXPIRY: int = 900   # 15 minutes

    # AWS Batch / Nextflow
    BATCH_JOB_QUEUE: str = "bioplatform-default"
    BATCH_JOB_ROLE_ARN: str = ""        # IAM role assumed by each Batch job container
    BATCH_INSTANCE_TYPE: str = "optimal" # informational label stored in results

    # Snakemake AWS Batch
    SNAKEMAKE_BATCH_QUEUE: str = ""      # defaults to BATCH_JOB_QUEUE if empty
    SNAKEMAKE_CONTAINER_IMAGE: str = "snakemake/snakemake:v8.20.0"

    # BioScript (bash runner)
    BIOSCRIPT_DOCKER_IMAGE: str = "bioplatform/tools:latest"  # custom image with bio tools

    # Auth / JWT
    JWT_SECRET: str = "change-this-secret-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_MINUTES: int = 60 * 24 * 7   # 7 days

    # CORS — comma-separated list of allowed origins
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # Stripe
    STRIPE_SECRET_KEY: str = ""           # sk_test_... or sk_live_...
    STRIPE_WEBHOOK_SECRET: str = ""       # whsec_...
    APP_BASE_URL: str = "http://localhost:5173"  # where Stripe redirects after payment

    # Email notifications
    EMAIL_PROVIDER: str = "log"          # log | ses | smtp
    EMAIL_FROM: str = "noreply@bioplatform.io"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SES_REGION: str = ""                 # defaults to AWS_REGION if empty

    # iyzico (Turkish payment gateway)
    IYZICO_API_KEY: str = ""
    IYZICO_SECRET_KEY: str = ""
    IYZICO_BASE_URL: str = "https://sandbox.iyzipay.com"
    IYZICO_USD_TO_TRY_RATE: float = 33.0

    # MFA / TOTP
    MFA_ISSUER: str = "BioplatformMD"

    # Sentry
    SENTRY_DSN: str = ""

    # Runtime mode
    DEBUG: bool = True


settings = Settings()
