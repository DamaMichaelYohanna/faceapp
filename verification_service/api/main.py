import os
import sys
import json
import traceback
import datetime
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import models, schemas, database
from core.face_engine import get_face_engine
from core.liveness import LivenessDetector
from core.faiss_service import get_faiss_service
from core.upstream_client import MasterClient, DomainClient
import security
import utils


# ---------------------------------------------------------------------------
# FAISS loader
# ---------------------------------------------------------------------------

def load_faiss_index(db: Session):
    svc = get_faiss_service()
    svc.clear()
    enrollments = db.query(models.FaceEnrollment).filter(
        models.FaceEnrollment.status == models.EnrollmentStatus.ACTIVE
    ).all()
    count = 0
    for e in enrollments:
        try:
            raw = security.decrypt_data(e.face_template)
            emb = np.frombuffer(raw, dtype=np.float32)
            svc.add_student(e.student_id, emb)
            count += 1
        except Exception as ex:
            print(f"FAISS load error student {e.student_id}: {ex}")
    print(f"FAISS loaded with {count} student(s).")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    models.Base.metadata.create_all(bind=database.engine)
    db = database.SessionLocal()
    try:
        if not db.query(models.SystemSettings).first():
            db.add(models.SystemSettings())
            db.commit()
        get_face_engine()
        load_faiss_index(db)
    finally:
        db.close()
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Biometric Verification Service",
    description="1:1 verification and 1:N identification. Fetches student data from domain servers.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    print(f"CRITICAL ERROR: {exc}")
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal Server Error: {str(exc)}"},
    )


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def get_settings(db: Session = Depends(database.get_db)):
    return db.query(models.SystemSettings).first()


def get_config(db: Session = Depends(database.get_db)) -> models.SystemConfig:
    cfg = db.query(models.SystemConfig).first()
    if not cfg:
        raise HTTPException(
            status_code=503,
            detail="Service not configured. POST /admin/config first.",
        )
    return cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_field(raw: Optional[str], name: str) -> Optional[Dict[str, Any]]:
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in '{name}'")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail=f"'{name}' must be a JSON object")
    return parsed


async def _read_and_validate_frames(files: List[UploadFile]) -> List[bytes]:
    frames: List[bytes] = []
    for idx, f in enumerate(files):
        content = await f.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"File at index {idx} is empty")
        if not utils.validate_image(content):
            raise HTTPException(status_code=400, detail=f"Invalid or low-quality image at index {idx}")
        frames.append(content)
    if not frames:
        raise HTTPException(status_code=400, detail="At least one image file is required")
    return frames


def _compute_liveness(frames: List[bytes], enabled: bool) -> Tuple[bool, bool]:
    if not enabled:
        return True, False
    return LivenessDetector.check_liveness(frames), True


async def _extract_embedding(files: List[UploadFile], liveness_enabled: bool):
    frames = await _read_and_validate_frames(files)
    liveness_passed, _ = _compute_liveness(frames, liveness_enabled)
    embedding = get_face_engine().get_embedding(frames[0])
    return embedding, liveness_passed


# ---------------------------------------------------------------------------
# Routes — Health
# ---------------------------------------------------------------------------

@app.get("/")
def read_root():
    return {"service": "Biometric Verification Service", "docs": "/docs"}


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "verification",
        "faiss_index_size": get_faiss_service().index.ntotal,
    }


# ---------------------------------------------------------------------------
# Routes — Admin Config
# ---------------------------------------------------------------------------

@app.get("/admin/config", response_model=schemas.SystemConfigResponse)
def get_admin_config(db: Session = Depends(database.get_db)):
    cfg = db.query(models.SystemConfig).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Not configured yet")
    return schemas.SystemConfigResponse(
        server_url=cfg.server_url,
        username=cfg.username,
        public_key=cfg.public_key,
        private_key_hint=f"...{cfg.private_key[-8:]}",
        is_configured=cfg.is_configured,
        updated_at=cfg.updated_at,
    )


@app.post("/admin/config", status_code=status.HTTP_201_CREATED)
def save_admin_config(
    payload: schemas.SystemConfigCreate,
    db: Session = Depends(database.get_db),
):
    cfg = db.query(models.SystemConfig).first()
    if cfg:
        cfg.server_url = payload.server_url
        cfg.username = payload.username
        cfg.public_key = payload.public_key
        cfg.private_key = payload.private_key
        cfg.aes_secret = payload.aes_secret
        cfg.is_configured = True
    else:
        cfg = models.SystemConfig(
            id=1,
            server_url=payload.server_url,
            username=payload.username,
            public_key=payload.public_key,
            private_key=payload.private_key,
            aes_secret=payload.aes_secret,
        )
        db.add(cfg)
    db.commit()
    return {"message": "Configuration saved successfully"}


@app.get("/admin/settings", response_model=schemas.SystemSettings)
def get_admin_settings(settings: models.SystemSettings = Depends(get_settings)):
    return settings


@app.put("/admin/settings", response_model=schemas.SystemSettings)
def update_admin_settings(
    payload: schemas.SystemSettingsUpdate,
    db: Session = Depends(database.get_db),
):
    s = db.query(models.SystemSettings).first()
    for k, v in payload.dict().items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return s


@app.post("/admin/reload-index")
def reload_index(db: Session = Depends(database.get_db)):
    load_faiss_index(db)
    return {"message": "FAISS index reloaded", "total": get_faiss_service().index.ntotal}


# ---------------------------------------------------------------------------
# Routes — Domain sync & browse
# ---------------------------------------------------------------------------

@app.post("/api/v1/domains/sync")
async def sync_domains(
    db: Session = Depends(database.get_db),
    cfg: models.SystemConfig = Depends(get_config),
):
    """Sync domain hierarchy (domains, departments, programme types, levels) from master."""
    master = MasterClient(cfg.server_url, cfg.public_key, cfg.private_key)
    raw_domains = await master.get_domains()

    synced = 0
    for d in raw_domains:
        host = d["host"]
        domain = db.query(models.CachedDomain).filter(models.CachedDomain.host == host).first()
        if not domain:
            domain = models.CachedDomain(host=host, identity=d["identity"], secret=d["secret"])
            db.add(domain)
        else:
            domain.identity = d["identity"]
            domain.secret = d["secret"]
            domain.synced_at = datetime.datetime.utcnow()
        db.commit()
        db.refresh(domain)

        client = DomainClient(domain.host, domain.identity, domain.secret)

        try:
            depts = await client.get_departments()
            db.query(models.CachedDepartment).filter(
                models.CachedDepartment.domain_id == domain.id
            ).delete()
            for dept in depts:
                db.add(models.CachedDepartment(
                    upstream_id=dept["id"],
                    domain_id=domain.id,
                    name=dept["name"],
                    code=dept.get("code"),
                ))
        except Exception as e:
            print(f"Warning: departments sync failed for {host}: {e}")

        try:
            pts = await client.get_programme_types()
            db.query(models.CachedProgrammeType).filter(
                models.CachedProgrammeType.domain_id == domain.id
            ).delete()
            for pt in pts:
                new_pt = models.CachedProgrammeType(
                    upstream_id=pt["id"],
                    domain_id=domain.id,
                    name=pt["name"],
                )
                db.add(new_pt)
                db.flush()
                try:
                    levels = await client.get_levels(pt["id"])
                    for lvl in levels:
                        db.add(models.CachedLevel(
                            upstream_id=lvl["id"],
                            programme_type_id=new_pt.id,
                            name=lvl["name"],
                        ))
                except Exception as le:
                    print(f"Warning: levels sync failed for pt={pt['id']}: {le}")
        except Exception as e:
            print(f"Warning: programme-types sync failed for {host}: {e}")

        db.commit()
        synced += 1

    return {"message": f"Synced {synced} domain(s)", "total": synced}


@app.get("/api/v1/domains", response_model=List[schemas.DomainResponse])
def list_domains(db: Session = Depends(database.get_db)):
    return db.query(models.CachedDomain).all()


@app.get("/api/v1/domains/{domain_id}/departments", response_model=List[schemas.DepartmentResponse])
def list_departments(domain_id: int, db: Session = Depends(database.get_db)):
    return db.query(models.CachedDepartment).filter(
        models.CachedDepartment.domain_id == domain_id
    ).all()


@app.get("/api/v1/domains/{domain_id}/programme-types", response_model=List[schemas.ProgrammeTypeResponse])
def list_programme_types(domain_id: int, db: Session = Depends(database.get_db)):
    return db.query(models.CachedProgrammeType).filter(
        models.CachedProgrammeType.domain_id == domain_id
    ).all()


@app.get(
    "/api/v1/domains/{domain_id}/programme-types/{pt_id}/levels",
    response_model=List[schemas.LevelResponse],
)
def list_levels(domain_id: int, pt_id: int, db: Session = Depends(database.get_db)):
    return db.query(models.CachedLevel).filter(
        models.CachedLevel.programme_type_id == pt_id
    ).all()


@app.get("/api/v1/domains/{domain_id}/users", response_model=List[schemas.UpstreamUser])
async def search_users(
    domain_id: int,
    department: int,
    level: Optional[int] = None,
    search: Optional[str] = None,
    db: Session = Depends(database.get_db),
):
    """Live user lookup from domain server."""
    domain = db.query(models.CachedDomain).filter(models.CachedDomain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found — run /api/v1/domains/sync first")
    client = DomainClient(domain.host, domain.identity, domain.secret)
    raw = await client.get_users(department, level_id=level, search=search)
    return [
        schemas.UpstreamUser(
            auth_id=u["authId"],
            user_id=u["userId"],
            name=u["name"],
            category=u["category"],
        )
        for u in raw
    ]


# ---------------------------------------------------------------------------
# Routes — 1:1 Verification
# ---------------------------------------------------------------------------

@app.post("/api/v1/verify/{identifier}", response_model=schemas.StandardResponse)
async def verify_student(
    identifier: str,
    file: UploadFile = File(...),
    extra_frames: List[UploadFile] = File([]),
    audit_info: Optional[str] = Form(None),
    db: Session = Depends(database.get_db),
    settings: models.SystemSettings = Depends(get_settings),
):
    """
    1:1 verification. `identifier` is the upstream authId (or internal numeric ID).
    The student must already have a face template stored by the capturing service.
    """
    student = None
    if identifier.isdigit():
        student = db.query(models.Student).filter(models.Student.id == int(identifier)).first()
    if not student:
        student = db.query(models.Student).filter(models.Student.external_id == identifier).first()
    if not student or not student.biometric_enrolled:
        raise HTTPException(status_code=404, detail="Student not found or not enrolled")

    embedding, liveness_passed = await _extract_embedding([file] + extra_frames, settings.liveness_enabled)
    if embedding is None:
        raise HTTPException(status_code=400, detail="No face detected")

    if settings.liveness_enabled and not liveness_passed:
        return schemas.StandardResponse(
            matched=False, student_id=student.id,
            external_id=student.external_id, full_name=student.full_name,
            confidence=0.0, mode="1:1", liveness_passed=False, message="Liveness check failed",
        )

    stored = student.enrollment
    if not stored:
        raise HTTPException(status_code=400, detail="No biometric template found")

    stored_emb = np.frombuffer(security.decrypt_data(stored.face_template), dtype=np.float32)
    confidence = float(np.dot(embedding, stored_emb))
    matched = confidence >= settings.similarity_threshold

    print(f"DEBUG: confidence={confidence:.4f}, threshold={settings.similarity_threshold}, matched={matched}")

    audit = _parse_json_field(audit_info, "audit_info")
    db.add(models.VerificationRecord(
        student_id=student.id,
        match_score=confidence,
        is_successful=matched,
        matching_mode="1:1",
        liveness_passed=liveness_passed,
        audit_metadata=audit,
    ))
    db.commit()

    return schemas.StandardResponse(
        matched=matched,
        student_id=student.id,
        external_id=student.external_id,
        full_name=student.full_name,
        confidence=confidence,
        mode="1:1",
        liveness_passed=liveness_passed,
        message="Match successful" if matched else "Match failed",
    )


# ---------------------------------------------------------------------------
# Routes — 1:N Identification
# ---------------------------------------------------------------------------

@app.post("/api/v1/identify", response_model=schemas.StandardResponse)
async def identify_student(
    file: UploadFile = File(...),
    extra_frames: List[UploadFile] = File([]),
    audit_info: Optional[str] = Form(None),
    db: Session = Depends(database.get_db),
    settings: models.SystemSettings = Depends(get_settings),
):
    """1:N identification — find the best-matching enrolled student."""
    embedding, liveness_passed = await _extract_embedding([file] + extra_frames, settings.liveness_enabled)
    if embedding is None:
        raise HTTPException(status_code=400, detail="No face detected")

    if settings.liveness_enabled and not liveness_passed:
        return schemas.StandardResponse(
            matched=False, confidence=0.0, mode="1:N",
            liveness_passed=False, message="Liveness check failed",
        )

    results = get_faiss_service().search(embedding, top_k=1)
    if not results:
        return schemas.StandardResponse(
            matched=False, confidence=0.0, mode="1:N",
            liveness_passed=liveness_passed, message="No candidates found",
        )

    best = results[0]
    confidence = best["confidence"]
    matched = confidence >= settings.similarity_threshold
    student_id = best["student_id"] if matched else None

    student = None
    if matched and student_id:
        student = db.query(models.Student).filter(models.Student.id == student_id).first()

    audit = _parse_json_field(audit_info, "audit_info")
    db.add(models.VerificationRecord(
        student_id=best["student_id"],
        match_score=confidence,
        is_successful=matched,
        matching_mode="1:N",
        liveness_passed=liveness_passed,
        audit_metadata={"search_results": results, "audit": audit or {}},
    ))
    db.commit()

    return schemas.StandardResponse(
        matched=matched,
        student_id=student_id,
        external_id=student.external_id if student else None,
        full_name=student.full_name if student else None,
        confidence=confidence,
        mode="1:N",
        liveness_passed=liveness_passed,
        message="Identified" if matched else "Identity not confirmed",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
