from fastapi import APIRouter
from app.api.v1 import auth, uploads, jobs, pipelines, nfcore, snakemake, payments, iyzico, patients

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router,      prefix="/auth",              tags=["auth"])
router.include_router(uploads.router,   prefix="/uploads",           tags=["uploads"])
router.include_router(jobs.router,      prefix="/jobs",              tags=["jobs"])
router.include_router(pipelines.router, prefix="/pipelines",         tags=["pipelines"])
router.include_router(nfcore.router,    prefix="/nfcore",            tags=["nfcore"])
router.include_router(snakemake.router, prefix="/snakemake",         tags=["snakemake"])
router.include_router(payments.router,  prefix="/payments",          tags=["payments"])
router.include_router(iyzico.router,    prefix="/payments/iyzico",   tags=["payments-iyzico"])
router.include_router(patients.router,  prefix="/patients",          tags=["patients"])
