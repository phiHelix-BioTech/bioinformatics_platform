"""Patient and Sample CRUD + FHIR R4 export.

Hierarchy: User → Patient(s) → Sample(s) → Job(s)

FHIR R4 resources returned as plain JSON dicts (no fhir library required).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.api.v1.deps import get_current_user
from app.database import get_db
from app.models.job import Job
from app.models.patient import Patient
from app.models.sample import Sample
from app.models.user import User

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class PatientCreate(BaseModel):
    name: str
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None      # M / F / Other
    notes: Optional[str] = None


class PatientOut(BaseModel):
    id: str
    name: str
    date_of_birth: Optional[date]
    sex: Optional[str]
    notes: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}


class SampleCreate(BaseModel):
    sample_type: str               # blood / saliva / tissue / ffpe / other
    collection_date: Optional[date] = None
    description: Optional[str] = None


class SampleOut(BaseModel):
    id: str
    patient_id: str
    sample_type: str
    collection_date: Optional[date]
    description: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}


# ── Patient endpoints ─────────────────────────────────────────────────────────

@router.get("", response_model=list[PatientOut])
async def list_patients(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Patient)
        .where(Patient.user_id == current_user.id)
        .order_by(desc(Patient.created_at))
    )
    return result.scalars().all()


@router.post("", response_model=PatientOut, status_code=201)
async def create_patient(
    body: PatientCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = Patient(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        name=body.name,
        date_of_birth=body.date_of_birth,
        sex=body.sex,
        notes=body.notes,
        created_at=datetime.now(timezone.utc),
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient


@router.get("/{patient_id}", response_model=PatientOut)
async def get_patient(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = await _get_owned_patient(db, patient_id, current_user.id)
    return patient


@router.put("/{patient_id}", response_model=PatientOut)
async def update_patient(
    patient_id: str,
    body: PatientCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = await _get_owned_patient(db, patient_id, current_user.id)
    patient.name = body.name
    patient.date_of_birth = body.date_of_birth
    patient.sex = body.sex
    patient.notes = body.notes
    await db.commit()
    await db.refresh(patient)
    return patient


@router.delete("/{patient_id}", status_code=204)
async def delete_patient(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = await _get_owned_patient(db, patient_id, current_user.id)
    await db.delete(patient)
    await db.commit()


# ── Sample endpoints ──────────────────────────────────────────────────────────

@router.get("/{patient_id}/samples", response_model=list[SampleOut])
async def list_samples(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_patient(db, patient_id, current_user.id)
    result = await db.execute(
        select(Sample)
        .where(Sample.patient_id == patient_id)
        .order_by(desc(Sample.created_at))
    )
    return result.scalars().all()


@router.post("/{patient_id}/samples", response_model=SampleOut, status_code=201)
async def create_sample(
    patient_id: str,
    body: SampleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_patient(db, patient_id, current_user.id)
    sample = Sample(
        id=str(uuid.uuid4()),
        patient_id=patient_id,
        user_id=current_user.id,
        sample_type=body.sample_type,
        collection_date=body.collection_date,
        description=body.description,
        created_at=datetime.now(timezone.utc),
    )
    db.add(sample)
    await db.commit()
    await db.refresh(sample)
    return sample


@router.get("/{patient_id}/samples/{sample_id}", response_model=SampleOut)
async def get_sample(
    patient_id: str,
    sample_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_patient(db, patient_id, current_user.id)
    return await _get_sample(db, sample_id, patient_id)


@router.put("/{patient_id}/samples/{sample_id}", response_model=SampleOut)
async def update_sample(
    patient_id: str,
    sample_id: str,
    body: SampleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_patient(db, patient_id, current_user.id)
    sample = await _get_sample(db, sample_id, patient_id)
    sample.sample_type = body.sample_type
    sample.collection_date = body.collection_date
    sample.description = body.description
    await db.commit()
    await db.refresh(sample)
    return sample


@router.delete("/{patient_id}/samples/{sample_id}", status_code=204)
async def delete_sample(
    patient_id: str,
    sample_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_patient(db, patient_id, current_user.id)
    sample = await _get_sample(db, sample_id, patient_id)
    await db.delete(sample)
    await db.commit()


# ── Jobs under patient ────────────────────────────────────────────────────────

@router.get("/{patient_id}/jobs")
async def list_patient_jobs(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all jobs linked to any sample of this patient."""
    await _get_owned_patient(db, patient_id, current_user.id)
    samples_result = await db.execute(
        select(Sample.id).where(Sample.patient_id == patient_id)
    )
    sample_ids = [r for r in samples_result.scalars().all()]
    if not sample_ids:
        return []
    jobs_result = await db.execute(
        select(Job)
        .where(Job.sample_id.in_(sample_ids))
        .order_by(desc(Job.created_at))
    )
    from app.api.v1.jobs import _serialize_job_list
    return [_serialize_job_list(j) for j in jobs_result.scalars().all()]


# ── FHIR R4 export ────────────────────────────────────────────────────────────

@router.get("/{patient_id}/fhir")
async def patient_fhir(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a FHIR R4 Patient resource for this patient."""
    patient = await _get_owned_patient(db, patient_id, current_user.id)
    resource: dict = {
        "resourceType": "Patient",
        "id": patient.id,
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/Patient"],
        },
        "name": [{"use": "official", "text": patient.name}],
    }
    if patient.date_of_birth:
        resource["birthDate"] = patient.date_of_birth.isoformat()
    if patient.sex:
        fhir_gender = {"M": "male", "F": "female"}.get(patient.sex.upper(), "other")
        resource["gender"] = fhir_gender
    if patient.notes:
        resource["extension"] = [{
            "url": "http://bioplatform.io/fhir/StructureDefinition/notes",
            "valueString": patient.notes,
        }]
    return resource


@router.get("/{patient_id}/samples/{sample_id}/fhir")
async def sample_fhir(
    patient_id: str,
    sample_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a FHIR R4 Specimen resource for this sample."""
    await _get_owned_patient(db, patient_id, current_user.id)
    sample = await _get_sample(db, sample_id, patient_id)
    resource: dict = {
        "resourceType": "Specimen",
        "id": sample.id,
        "subject": {"reference": f"Patient/{patient_id}"},
        "type": {
            "coding": [{
                "system": "http://snomed.info/sct",
                "display": sample.sample_type,
            }]
        },
    }
    if sample.collection_date:
        resource["collection"] = {"collectedDateTime": sample.collection_date.isoformat()}
    if sample.description:
        resource["note"] = [{"text": sample.description}]
    return resource


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_owned_patient(db: AsyncSession, patient_id: str, user_id: str) -> Patient:
    result = await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.user_id == user_id)
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return patient


async def _get_sample(db: AsyncSession, sample_id: str, patient_id: str) -> Sample:
    result = await db.execute(
        select(Sample).where(Sample.id == sample_id, Sample.patient_id == patient_id)
    )
    sample = result.scalar_one_or_none()
    if sample is None:
        raise HTTPException(status_code=404, detail="Sample not found.")
    return sample
