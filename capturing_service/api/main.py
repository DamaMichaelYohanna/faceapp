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
from core.upstream_client import MasterClient, DomainClient
from core.aes_crypto import encrypt_template, encrypt_username
import security
import utils

MIN_ACTIVE_LIVENESS_FRAMES = 3


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
    finally:
        db.close()
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Biometric Capturing Service",
    description="Offline-first face enrollment. Captures locally then syncs to master server.",
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


# ---------------------------------------------------------------------------
# Routes — Health
# ---------------------------------------------------------------------------

@app.get("/")
def read_root():
    return {"service": "Biometric Capturing Service", "docs": "/docs"}


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "capturing"}


# ---------------------------------------------------------------------------
# Routes — Admin Config
# ---------------------------------------------------------------------------

@app.get("/admin/config", response_model=schemas.SystemConfigResponse)
def get_admin_config(db: Session = Depends(database.get_db)):
    """Return current master-server configuration (private key is masked)."""
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
    """Save (or replace) master-server credentials."""
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


# ---------------------------------------------------------------------------
# Routes — Domain cache (sync from upstream)
# ---------------------------------------------------------------------------

@app.post("/api/v1/domains/sync")
async def sync_domains(
    db: Session = Depends(database.get_db),
    cfg: models.SystemConfig = Depends(get_config),
):
    """
    Pull domains from the master server, then for each domain pull departments,
    programme types and levels. Results are cached locally for offline use.
    """
    master = MasterClient(cfg.server_url, cfg.public_key, cfg.private_key)
    raw_domains = await master.get_domains()

    synced_domains = 0
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

        # --- Departments ---
        try:
            depts = await client.get_departments()
            # Delete stale departments for this domain
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
            print(f"Warning: could not sync departments for {host}: {e}")

        # --- Programme types + levels ---
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
                db.flush()  # get new_pt.id

                try:
                    levels = await client.get_levels(pt["id"])
                    for lvl in levels:
                        db.add(models.CachedLevel(
                            upstream_id=lvl["id"],
                            programme_type_id=new_pt.id,
                            name=lvl["name"],
                        ))
                except Exception as le:
                    print(f"Warning: could not sync levels for pt={pt['id']}: {le}")
        except Exception as e:
            print(f"Warning: could not sync programme types for {host}: {e}")

        db.commit()
        synced_domains += 1

    return {"message": f"Synced {synced_domains} domain(s)", "total": synced_domains}


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


# ---------------------------------------------------------------------------
# Routes — Live user lookup from domain API
# ---------------------------------------------------------------------------

@app.get("/api/v1/users", response_model=List[schemas.UpstreamUser])
async def search_users(
    domain_id: int,
    department: int,
    level: Optional[int] = None,
    search: Optional[str] = None,
    db: Session = Depends(database.get_db),
    cfg: models.SystemConfig = Depends(get_config),
):
    """
    Fetch students/staff live from a domain server and cache them locally.
    Always call this before enrollment to ensure CachedStudent is up to date.
    """
    domain = db.query(models.CachedDomain).filter(models.CachedDomain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found — run /api/v1/domains/sync first")

    client = DomainClient(domain.host, domain.identity, domain.secret)
    raw_users = await client.get_users(department, level_id=level, search=search)

    # Cache / upsert each user
    for u in raw_users:
        cached = db.query(models.CachedStudent).filter(
            models.CachedStudent.auth_id == u["authId"]
        ).first()
        if cached:
            cached.user_id = u["userId"]
            cached.name = u["name"]
            cached.category = u["category"]
            cached.department_upstream_id = department
            cached.last_seen_at = datetime.datetime.utcnow()
        else:
            db.add(models.CachedStudent(
                auth_id=u["authId"],
                user_id=u["userId"],
                name=u["name"],
                category=u["category"],
                domain_id=domain_id,
                department_upstream_id=department,
            ))
    db.commit()

    return [
        schemas.UpstreamUser(
            auth_id=u["authId"],
            user_id=u["userId"],
            name=u["name"],
            category=u["category"],
        )
        for u in raw_users
    ]


# ---------------------------------------------------------------------------
# Routes — Students
# ---------------------------------------------------------------------------

@app.get("/api/v1/students/", response_model=List[schemas.Student])
def list_students(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(database.get_db),
):
    return db.query(models.Student).offset(skip).limit(limit).all()


# ---------------------------------------------------------------------------
# Routes — Enrollment (offline-first)
# ---------------------------------------------------------------------------

async def _enroll_face_impl(
    db: Session,
    settings: models.SystemSettings,
    auth_id: Optional[str],
    metadata: Optional[str],
    single_file: Optional[UploadFile],
    multiple_files: Optional[List[UploadFile]],
):
    if not auth_id or not auth_id.strip():
        raise HTTPException(status_code=400, detail="'auth_id' (upstream authId) is required")
    auth_id = auth_id.strip()

    files: List[UploadFile] = []
    if single_file:
        files.append(single_file)
    if multiple_files:
        files.extend(multiple_files)

    # 1. Validate frames
    frames = await _read_and_validate_frames(files)
    if settings.liveness_enabled and len(frames) < MIN_ACTIVE_LIVENESS_FRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Active liveness requires at least {MIN_ACTIVE_LIVENESS_FRAMES} frames.",
        )

    liveness_passed, liveness_checked = _compute_liveness(frames, settings.liveness_enabled)
    if liveness_checked and not liveness_passed:
        raise HTTPException(status_code=400, detail="Liveness check failed.")

    # 2. Resolve student from local cache (offline-first)
    cached = db.query(models.CachedStudent).filter(
        models.CachedStudent.auth_id == auth_id
    ).first()
    if not cached:
        raise HTTPException(
            status_code=404,
            detail=f"Student '{auth_id}' not in local cache. Call /api/v1/users to fetch from domain first.",
        )

    student = db.query(models.Student).filter(models.Student.external_id == auth_id).first()
    if not student:
        student = models.Student(external_id=auth_id, full_name=cached.name)
        db.add(student)
        db.commit()
        db.refresh(student)

    # 3. Extract embedding
    engine = get_face_engine()
    embedding = engine.get_embedding(frames[0])
    if embedding is None:
        raise HTTPException(status_code=400, detail="No face detected in image")

    print(f"DEBUG: Embedding shape={embedding.shape}, norm={np.linalg.norm(embedding):.4f}")

    # 4. Encrypt and store (Fernet for local storage)
    encrypted_template = security.encrypt_data(embedding.tobytes())
    metadata_dict = _parse_json_field(metadata, "metadata") or {}
    metadata_dict.update({
        "engine": "insightface_buffalo_l",
        "image_hash": utils.get_image_hash(frames[0]),
        "liveness_checked": liveness_checked,
        "liveness_passed": liveness_passed,
        "frames_received": len(frames),
    })

    try:
        db_enrollment = db.query(models.FaceEnrollment).filter(
            models.FaceEnrollment.student_id == student.id
        ).first()
        if db_enrollment:
            db_enrollment.face_template = encrypted_template
            db_enrollment.status = models.EnrollmentStatus.ACTIVE
            db_enrollment.metadata_json = metadata_dict
            db_enrollment.upload_status = models.UploadStatus.PENDING
            db_enrollment.uploaded_at = None
            db_enrollment.upload_error = None
        else:
            db_enrollment = models.FaceEnrollment(
                student_id=student.id,
                face_template=encrypted_template,
                status=models.EnrollmentStatus.ACTIVE,
                metadata_json=metadata_dict,
                upload_status=models.UploadStatus.PENDING,
            )
            db.add(db_enrollment)

        student.biometric_enrolled = True
        db.commit()

        return schemas.EnrollResponse(
            success=True,
            status="success",
            message=f"Face enrollment saved locally for {student.full_name}. Pending upload to master.",
            student_id=student.id,
            external_id=student.external_id,
            liveness_passed=liveness_passed,
            liveness_checked=liveness_checked,
            upload_status=schemas.UploadStatus.PENDING,
        )
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"DB or Engine error: {str(e)}")


@app.post("/api/v1/enroll", response_model=schemas.EnrollResponse, status_code=status.HTTP_201_CREATED)
async def enroll_face(
    auth_id: Optional[str] = Form(None, description="Upstream authId of the student"),
    metadata: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
    db: Session = Depends(database.get_db),
    settings: models.SystemSettings = Depends(get_settings),
):
    """Enroll a student's face offline. Student must be cached via /api/v1/users first."""
    return await _enroll_face_impl(db, settings, auth_id, metadata, None, files)


@app.post("/api/v1/enroll/upload", response_model=schemas.EnrollResponse, status_code=status.HTTP_201_CREATED)
async def enroll_face_upload(
    auth_id: Optional[str] = Form(None),
    metadata: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    files: List[UploadFile] = File([]),
    db: Session = Depends(database.get_db),
    settings: models.SystemSettings = Depends(get_settings),
):
    """Alternate enrollment endpoint accepting a single file or multiple files."""
    return await _enroll_face_impl(db, settings, auth_id, metadata, file, files)


# ---------------------------------------------------------------------------
# Routes — Upload sync (offline → master server)
# ---------------------------------------------------------------------------

@app.get("/api/v1/upload-sync/status", response_model=schemas.SyncStatusResponse)
def sync_status(db: Session = Depends(database.get_db)):
    """Count enrollments by upload status."""
    def count(s):
        return db.query(models.FaceEnrollment).filter(
            models.FaceEnrollment.upload_status == s
        ).count()

    return schemas.SyncStatusResponse(
        pending=count(models.UploadStatus.PENDING),
        uploaded=count(models.UploadStatus.UPLOADED),
        failed=count(models.UploadStatus.FAILED),
    )


@app.post("/api/v1/upload-sync", response_model=schemas.SyncResultResponse)
async def upload_sync(
    db: Session = Depends(database.get_db),
    cfg: models.SystemConfig = Depends(get_config),
):
    """
    Upload all PENDING enrollments to the master server.

    For each enrollment:
    1. Decrypt the Fernet-encrypted face template.
    2. AES-encrypt the raw bytes (base64 first) with the AES secret.
    3. AES-encrypt the operator username.
    4. POST to master /api/v1/enrollment/byte/upload.
    5. Mark the enrollment UPLOADED or FAILED.
    """
    pending = db.query(models.FaceEnrollment).filter(
        models.FaceEnrollment.upload_status == models.UploadStatus.PENDING
    ).all()

    if not pending:
        return schemas.SyncResultResponse(attempted=0, succeeded=0, failed=0, errors=[])

    master = MasterClient(cfg.server_url, cfg.public_key, cfg.private_key)
    encrypted_user = encrypt_username(cfg.username, cfg.aes_secret)

    attempted = succeeded = 0
    errors: List[str] = []

    for enrollment in pending:
        attempted += 1
        enrollment.upload_status = models.UploadStatus.UPLOADING
        db.commit()

        try:
            # Decrypt from local Fernet storage
            raw_bytes = security.decrypt_data(enrollment.face_template)

            # Build the prints payload entry
            student = enrollment.student
            cached = db.query(models.CachedStudent).filter(
                models.CachedStudent.auth_id == student.external_id
            ).first()

            prints_entry = {
                "finger": "FACE",          # face template — using FACE as identifier
                "data": encrypt_template(raw_bytes, cfg.aes_secret),
                "identification": student.external_id,  # upstream authId
                "name": student.full_name,
                "captureDate": (enrollment.created_at or datetime.datetime.utcnow()).strftime(
                    "%Y-%m-%dT%H:%M:%S.%f"
                )[:-3],
                "category": cached.category if cached else "STUDENT",
            }

            await master.upload_fingerprints(encrypted_user, [prints_entry])

            enrollment.upload_status = models.UploadStatus.UPLOADED
            enrollment.uploaded_at = datetime.datetime.utcnow()
            enrollment.upload_error = None
            db.commit()
            succeeded += 1

        except Exception as e:
            db.rollback()
            error_msg = f"Student {enrollment.student_id}: {str(e)}"
            errors.append(error_msg)
            print(f"Upload failed — {error_msg}")

            enrollment.upload_status = models.UploadStatus.FAILED
            enrollment.upload_error = str(e)
            db.commit()

    return schemas.SyncResultResponse(
        attempted=attempted,
        succeeded=succeeded,
        failed=attempted - succeeded,
        errors=errors,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
