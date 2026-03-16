from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from app.api.v1.router import router
from app.config import settings
from app.database import engine, Base, AsyncSessionLocal
from app.limiter import limiter


_DEFAULT_JWT_SECRET = "change-this-secret-in-production"

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=0.2,
        environment="production" if not settings.DEBUG else "development",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to start in production with the default JWT secret
    if not settings.DEBUG and settings.JWT_SECRET == _DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET is set to the default insecure value. "
            "Set a strong random secret in your environment before deploying."
        )
    # Create any tables not yet in the DB (idempotent; Alembic handles schema changes)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, checkfirst=True))
    except Exception:
        pass  # tables already exist from a previous migration run
    # Kick off nf-core catalog scrape in background (skips if already loaded)
    try:
        from app.tasks.scrape_nfcore import scrape_nfcore_catalog
        scrape_nfcore_catalog.delay()
    except Exception:
        pass  # worker may not be ready yet; catalog can be refreshed via POST /nfcore/refresh
    # Kick off Snakemake catalog scrape in background (skips if already loaded)
    try:
        from app.tasks.scrape_snakemake import scrape_snakemake_catalog
        scrape_snakemake_catalog.delay()
    except Exception:
        pass  # worker may not be ready yet; catalog can be refreshed via POST /snakemake/refresh
    yield
    await engine.dispose()


app = FastAPI(
    title="Bioinformatics Platform API",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.get("/health")
async def health():
    """Deep health check: verify DB connectivity and Redis availability."""
    db_ok = False
    redis_ok = False

    try:
        async with AsyncSessionLocal() as s:
            await s.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    try:
        import redis as _redis
        _redis.from_url(settings.CELERY_BROKER_URL).ping()
        redis_ok = True
    except Exception:
        pass

    ok = db_ok and redis_ok
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "db": db_ok, "redis": redis_ok},
        status_code=200 if ok else 503,
    )
