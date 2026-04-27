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
            
        # Create default admin if none exists
        admin = db.query(models.Admin).first()
        if not admin:
            default_admin = models.Admin(
                username=os.getenv("ADMIN_USERNAME", "admin"),
                hashed_password=security.get_password_hash(os.getenv("ADMIN_PASSWORD", "admin123"))
            )
            db.add(default_admin)
            
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

from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt

# --- Dependency ---

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/admin/login")

def get_current_admin(token: str = Depends(oauth2_scheme), db: Session = Depends(database.get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, security.JWT_SECRET_KEY, algorithms=[security.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    admin = db.query(models.Admin).filter(models.Admin.username == username).first()
    if admin is None or not admin.is_active:
        raise credentials_exception
    return admin

def get_settings(db: Session = Depends(database.get_db)):
    return db.query(models.SystemSettings).first()

# --- Public & Auth API ---

@app.post("/api/v1/admin/login", response_model=schemas.Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(database.get_db)):
    admin = db.query(models.Admin).filter(models.Admin.username == form_data.username).first()
    if not admin or not security.verify_password(form_data.password, admin.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not admin.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
        
    access_token = security.create_access_token(
        data={"sub": admin.username}
    )
    return {"access_token": access_token, "token_type": "bearer"}

# --- Public API ---

@app.get("/")
def read_root():
    return {"message": "Welcome to the Production Face Biometric API", "docs": "/docs"}

# --- Student Management (Existing) ---

@app.post("/api/v1/students/", response_model=schemas.Student)
def create_student(
    student: schemas.StudentCreate, 
    db: Session = Depends(database.get_db),
    admin: models.Admin = Depends(get_current_admin)
):
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
def list_students(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(database.get_db),
    admin: models.Admin = Depends(get_current_admin)
):
    return db.query(models.Student).offset(skip).limit(limit).all()

# --- Biometric Enrollment ---

@app.post("/api/v1/enroll/upload", status_code=status.HTTP_201_CREATED)
async def enroll_face(
    matric_number: str = Form(...),
    metadata: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db),
    admin: models.Admin = Depends(get_current_admin)
):
    import httpx
    
    # 1. Fetch from Main Storage API
    external_api_url = os.getenv("EXTERNAL_STUDENT_API_URL", "http://localhost:8080/api/students/")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{external_api_url}{matric_number}")
            if response.status_code != 200:
                raise HTTPException(status_code=404, detail=f"Student {matric_number} not found in main storage")
            student_data = response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with main storage: {str(e)}")

    # 2. Sync with local database
    student = db.query(models.Student).filter(models.Student.external_id == matric_number).first()
    if not student:
        # Create student locally if they exist in main storage but not here
        # Assuming the external API returns a 'full_name' field
        full_name = student_data.get("full_name", "Unknown Name")
        student = models.Student(
            external_id=matric_number,
            full_name=full_name
        )
        db.add(student)
        db.commit()
        db.refresh(student)

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
    db_enrollment = db.query(models.FaceEnrollment).filter(models.FaceEnrollment.student_id == student.id).first()
    if db_enrollment:
        db_enrollment.face_template = encrypted_template
        db_enrollment.status = models.EnrollmentStatus.ACTIVE
        db_enrollment.metadata_json = metadata_dict
    else:
        db_enrollment = models.FaceEnrollment(
            student_id=student.id,
            face_template=encrypted_template,
            status=models.EnrollmentStatus.ACTIVE,
            metadata_json=metadata_dict
        )
        db.add(db_enrollment)

    student.biometric_enrolled = True
    db.commit()
    
    # Update FAISS
    get_faiss_service().add_student(student.id, embedding)
    
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
    settings: models.SystemSettings = Depends(get_settings),
    admin: models.Admin = Depends(get_current_admin)
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
    settings: models.SystemSettings = Depends(get_settings),
    admin: models.Admin = Depends(get_current_admin)
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
def get_admin_settings(
    settings: models.SystemSettings = Depends(get_settings),
    admin: models.Admin = Depends(get_current_admin)
):
    return settings

@app.put("/admin/settings", response_model=schemas.SystemSettings)
def update_admin_settings(
    payload: schemas.SystemSettingsUpdate, 
    db: Session = Depends(database.get_db),
    admin: models.Admin = Depends(get_current_admin)
):
    settings = db.query(models.SystemSettings).first()
    for field, value in payload.dict().items():
        setattr(settings, field, value)
    db.commit()
    db.refresh(settings)
    return settings

@app.post("/admin/reload-index")
def reload_index(
    db: Session = Depends(database.get_db),
    admin: models.Admin = Depends(get_current_admin)
):
    load_faiss_index(db)
    return {"message": "FAISS index reloaded successfully"}

@app.get("/health")
def health_check():
    return {"status": "healthy", "engine": "loaded", "faiss_size": get_faiss_service().index.ntotal}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
