import os
import sys
from contextlib import asynccontextmanager
from typing import List, Optional
import json

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.orm import Session
import numpy as np

# Adjust path to import from parent directory if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import models, schemas, database
from core.face_engine import get_face_engine
from core.liveness import LivenessDetector
from core.faiss_service import get_faiss_service
import security, utils

# --- Initialization & Lifespan ---

def load_faiss_index(db: Session):
    """Load all active enrollments into FAISS index."""
    faiss_service = get_faiss_service()
    faiss_service.clear()
    
    enrollments = db.query(models.FaceEnrollment).filter(models.FaceEnrollment.status == models.EnrollmentStatus.ACTIVE).all()
    count = 0
    for enrollment in enrollments:
        try:
            # Decrypt template
            template_bytes = security.decrypt_data(enrollment.face_template)
            embedding = np.frombuffer(template_bytes, dtype=np.float32)
            faiss_service.add_student(enrollment.student_id, embedding)
            count += 1
        except Exception as e:
            print(f"Error loading enrollment for student {enrollment.student_id}: {e}")
    
    print(f"FAISS index loaded with {count} students.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    models.Base.metadata.create_all(bind=database.engine)
    
    # Initialize settings if not present
    db = database.SessionLocal()
    try:
        settings = db.query(models.SystemSettings).first()
        if not settings:
            settings = models.SystemSettings()
            db.add(settings)
            db.commit()
        
        # Warm up Face Engine
        get_face_engine()
        
        # Load FAISS
        load_faiss_index(db)
    finally:
        db.close()
        
    yield
    # Shutdown logic (if any)

app = FastAPI(
    title="Production Face Biometric API",
    description="Secure 1:1 and 1:N biometric authentication with liveness detection.",
    version="2.0.0",
    lifespan=lifespan
)

# --- Dependency ---

def get_settings(db: Session = Depends(database.get_db)):
    return db.query(models.SystemSettings).first()

# --- Public API ---

@app.get("/")
def read_root():
    return {"message": "Welcome to the Production Face Biometric API", "docs": "/docs"}

# --- Student Management (Existing) ---

@app.post("/api/v1/students/", response_model=schemas.Student)
def create_student(student: schemas.StudentCreate, db: Session = Depends(database.get_db)):
    db_student = db.query(models.Student).filter(models.Student.external_id == student.external_id).first()
    if db_student:
        raise HTTPException(status_code=400, detail="Student already registered")
    
    new_student = models.Student(
        external_id=student.external_id,
        full_name=student.full_name
    )
    db.add(new_student)
    db.commit()
    db.refresh(new_student)
    return new_student

@app.get("/api/v1/students/", response_model=List[schemas.Student])
def list_students(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    return db.query(models.Student).offset(skip).limit(limit).all()

# --- Biometric Enrollment ---

@app.post("/api/v1/enroll/upload", status_code=status.HTTP_201_CREATED)
async def enroll_face(
    student_id: int = Form(...),
    metadata: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db)
):
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    contents = await file.read()
    engine = get_face_engine()
    
    # Real extraction
    embedding = engine.get_embedding(contents)
    if embedding is None:
        raise HTTPException(status_code=400, detail="No face detected in image")

    # Encrypt
    encrypted_template = security.encrypt_data(embedding.tobytes())

    # Metadata
    metadata_dict = json.loads(metadata) if metadata else {}
    metadata_dict.update({
        "engine": "insightface_buffalo_l",
        "image_hash": utils.get_image_hash(contents)
    })

    # Save to DB
    db_enrollment = db.query(models.FaceEnrollment).filter(models.FaceEnrollment.student_id == student_id).first()
    if db_enrollment:
        db_enrollment.face_template = encrypted_template
        db_enrollment.status = models.EnrollmentStatus.ACTIVE
        db_enrollment.metadata_json = metadata_dict
    else:
        db_enrollment = models.FaceEnrollment(
            student_id=student_id,
            face_template=encrypted_template,
            status=models.EnrollmentStatus.ACTIVE,
            metadata_json=metadata_dict
        )
        db.add(db_enrollment)

    student.biometric_enrolled = True
    db.commit()
    
    # Update FAISS
    get_faiss_service().add_student(student_id, embedding)
    
    return {"status": "success", "message": "Face enrollment completed"}

# --- Verification & Identification ---

async def process_verification_images(files: List[UploadFile], liveness_enabled: bool) -> (np.ndarray, bool):
    """Helper to handle multiple frames and liveness."""
    frames_content = []
    for f in files:
        frames_content.append(await f.read())
    
    # Check Liveness if enabled
    liveness_passed = True
    if liveness_enabled:
        liveness_passed = LivenessDetector.check_liveness(frames_content)
    
    # Get embedding from the first frame
    engine = get_face_engine()
    embedding = engine.get_embedding(frames_content[0])
    
    return embedding, liveness_passed

@app.post("/api/v1/verify/{student_id}", response_model=schemas.StandardResponse)
async def verify_student(
    student_id: int,
    images: List[UploadFile] = File(...),
    audit_info: Optional[str] = Form(None),
    db: Session = Depends(database.get_db),
    settings: models.SystemSettings = Depends(get_settings)
):
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not student or not student.biometric_enrolled:
        raise HTTPException(status_code=400, detail="Student not enrolled")

    # 1. Process Images & Liveness
    embedding, liveness_passed = await process_verification_images(images, settings.liveness_enabled)
    
    if embedding is None:
        raise HTTPException(status_code=400, detail="No face detected")
    
    if settings.liveness_enabled and not liveness_passed:
        return schemas.StandardResponse(
            matched=False, student_id=student_id, confidence=0.0,
            mode="1:1", liveness_passed=False, message="Liveness check failed"
        )

    # 2. Get stored template
    stored_enrollment = student.enrollment
    decrypted_stored_template = security.decrypt_data(stored_enrollment.face_template)
    stored_embedding = np.frombuffer(decrypted_stored_template, dtype=np.float32)

    # 3. Match
    confidence = float(np.dot(embedding, stored_embedding)) # Both are pre-normalized
    matched = confidence >= settings.similarity_threshold

    # 4. Log
    log = models.VerificationRecord(
        student_id=student_id,
        match_score=confidence,
        is_successful=matched,
        matching_mode="1:1",
        liveness_passed=liveness_passed,
        audit_metadata=json.loads(audit_info) if audit_info else None
    )
    db.add(log)
    db.commit()

    return schemas.StandardResponse(
        matched=matched,
        student_id=student_id,
        confidence=confidence,
        mode="1:1",
        liveness_passed=liveness_passed,
        message="Match successful" if matched else "Match failed"
    )

@app.post("/api/v1/identify", response_model=schemas.StandardResponse)
async def identify_student(
    images: List[UploadFile] = File(...),
    audit_info: Optional[str] = Form(None),
    db: Session = Depends(database.get_db),
    settings: models.SystemSettings = Depends(get_settings)
):
    # 1. Process Images
    embedding, liveness_passed = await process_verification_images(images, settings.liveness_enabled)
    
    if embedding is None:
        raise HTTPException(status_code=400, detail="No face detected")
    
    if settings.liveness_enabled and not liveness_passed:
        return schemas.StandardResponse(
            matched=False, confidence=0.0, mode="1:N",
            liveness_passed=False, message="Liveness check failed"
        )

    # 2. Search FAISS
    results = get_faiss_service().search(embedding, top_k=1)
    
    if not results:
        return schemas.StandardResponse(
            matched=False, confidence=0.0, mode="1:N",
            liveness_passed=liveness_passed, message="No candidates found"
        )

    best_match = results[0]
    confidence = best_match["confidence"]
    matched = confidence >= settings.similarity_threshold
    student_id = best_match["student_id"] if matched else None

    # 3. Log
    log = models.VerificationRecord(
        student_id=best_match["student_id"],
        match_score=confidence,
        is_successful=matched,
        matching_mode="1:N",
        liveness_passed=liveness_passed,
        audit_metadata={"search_results": results, "audit": json.loads(audit_info) if audit_info else {}}
    )
    db.add(log)
    db.commit()

    return schemas.StandardResponse(
        matched=matched,
        student_id=student_id,
        confidence=confidence,
        mode="1:N",
        liveness_passed=liveness_passed,
        message="Identified" if matched else "Identity not confirmed"
    )

# --- Admin API ---

@app.get("/admin/settings", response_model=schemas.SystemSettings)
def get_admin_settings(settings: models.SystemSettings = Depends(get_settings)):
    return settings

@app.put("/admin/settings", response_model=schemas.SystemSettings)
def update_admin_settings(payload: schemas.SystemSettingsUpdate, db: Session = Depends(database.get_db)):
    settings = db.query(models.SystemSettings).first()
    for field, value in payload.dict().items():
        setattr(settings, field, value)
    db.commit()
    db.refresh(settings)
    return settings

@app.post("/admin/reload-index")
def reload_index(db: Session = Depends(database.get_db)):
    load_faiss_index(db)
    return {"message": "FAISS index reloaded successfully"}

@app.get("/health")
def health_check():
    return {"status": "healthy", "engine": "loaded", "faiss_size": get_faiss_service().index.ntotal}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
