import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple
import json
import traceback

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

MIN_ACTIVE_LIVENESS_FRAMES = 3

from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt

# Allow all origins so the HTML frontend (opened as file://) can reach the API.
# Restrict origins to your domain in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    print(f"CRITICAL ERROR: {exc}")
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal Server Error: {str(exc)}", "traceback": traceback.format_exc()}
    )

# --- Public API ---

@app.get("/")
def read_root():
    return {"message": "Welcome to the Production Face Biometric API", "docs": "/docs"}

# --- Mock / External Student Lookup ---

# A small in-memory mock registry for development.
# Populate this or set USE_MOCK_EXTERNAL_API=false to use the real external API.
MOCK_STUDENTS = {
    "FT22ACMP0833": {"full_name": "Michael Dama"},
    "FT22ACMP0843": {"full_name": "Abdul Jaleel"},
    "FT22ACMP0803": {"full_name": "Chinedu Okafor"},
    "FT22ACMP0804": {"full_name": "Fatima Al-Hassan"},
    "FT22ACMP0805": {"full_name": "David Musa"},
}

async def fetch_student_from_external(matric_number: str) -> dict:
    """
    Fetches student data either from the real external API or a local mock,
    depending on the USE_MOCK_EXTERNAL_API environment variable.
    Returns a dict with at least a 'full_name' key, or raises HTTPException.
    """
    use_mock = os.getenv("USE_MOCK_EXTERNAL_API", "true").lower() == "true"

    if use_mock:
        student_data = MOCK_STUDENTS.get(matric_number)
        if student_data:
            return student_data

        raise HTTPException(
            status_code=404,
            detail=(
                f"[MOCK] Student '{matric_number}' not found. "
                f"Allowed IDs: {list(MOCK_STUDENTS.keys())}"
            ),
        )

    # --- Real external API call ---
    import httpx
    external_api_url = os.getenv("EXTERNAL_STUDENT_API_URL", "http://localhost:8080/api/students/")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{external_api_url}{matric_number}")
            if response.status_code != 200:
                raise HTTPException(status_code=404, detail=f"Student '{matric_number}' not found in main storage")
            return response.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with main storage: {str(e)}")


def _parse_json_form_field(raw_value: Optional[str], field_name: str) -> Optional[Dict[str, Any]]:
    """Parse an optional JSON form string and raise an HTTP 400 on invalid payloads."""
    if not raw_value or not raw_value.strip():
        return None
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in '{field_name}'")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail=f"'{field_name}' must be a JSON object")
    return parsed


def _normalize_identifier(matric_number: Optional[str], external_id: Optional[str]) -> str:
    """Accept either matric_number or external_id and return a validated normalized value."""
    chosen = (matric_number or external_id or "").strip()
    if not chosen:
        raise HTTPException(status_code=400, detail="Either 'matric_number' or 'external_id' is required")
    if len(chosen) < 3 or len(chosen) > 64:
        raise HTTPException(status_code=400, detail="Identifier length must be between 3 and 64 characters")
    return chosen


async def _read_and_validate_frames(files: List[UploadFile]) -> List[bytes]:
    """Read uploaded files and validate each frame as a usable image."""
    frames_content: List[bytes] = []
    for idx, uploaded in enumerate(files):
        content = await uploaded.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"Uploaded file at index {idx} is empty")
        if not utils.validate_image(content):
            raise HTTPException(status_code=400, detail=f"Invalid or low-quality image at index {idx}")
        frames_content.append(content)

    if not frames_content:
        raise HTTPException(status_code=400, detail="At least one image file is required")
    return frames_content


def _compute_liveness(frames_content: List[bytes], liveness_enabled: bool) -> Tuple[bool, bool]:
    """Return (liveness_passed, liveness_checked). Liveness is skipped only when disabled."""
    if not liveness_enabled:
        return True, False
    return LivenessDetector.check_liveness(frames_content), True

# --- Student Management ---

@app.get("/api/v1/students/", response_model=List[schemas.Student])
def list_students(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(database.get_db),
    admin: models.Admin = Depends(get_current_admin)
):
    return db.query(models.Student).offset(skip).limit(limit).all()

# --- Biometric Enrollment ---

async def _enroll_face_impl(
    db: Session,
    settings: models.SystemSettings,
    matric_number: Optional[str],
    external_id: Optional[str],
    metadata: Optional[str],
    single_file: Optional[UploadFile],
    multiple_files: Optional[List[UploadFile]],
):
    matric = _normalize_identifier(matric_number, external_id)
    files: List[UploadFile] = []
    if single_file is not None:
        files.append(single_file)
    if multiple_files:
        files.extend(multiple_files)

    # 1. Read frames, validate image quality baseline and run liveness.
    frames_content = await _read_and_validate_frames(files)
    if settings.liveness_enabled and len(frames_content) < MIN_ACTIVE_LIVENESS_FRAMES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Active liveness requires at least {MIN_ACTIVE_LIVENESS_FRAMES} frames. "
                "Retake while the subject blinks or turns slightly."
            ),
        )

    liveness_passed, liveness_checked = _compute_liveness(frames_content, settings.liveness_enabled)
    if liveness_checked and not liveness_passed:
        raise HTTPException(
            status_code=400,
            detail="Liveness check failed. Ensure the subject is physically present and retake.",
        )

    # 2. Fetch from external source (real API or mock)
    student_data = await fetch_student_from_external(matric)

    # 3. Sync with local database
    student = db.query(models.Student).filter(models.Student.external_id == matric).first()
    if not student:
        # Create student locally if they exist in main storage but not here
        # Assuming the external API returns a 'full_name' field
        full_name = student_data.get("full_name", "Unknown Name")
        student = models.Student(
            external_id=matric,
            full_name=full_name
        )
        db.add(student)
        db.commit()
        db.refresh(student)

    contents = frames_content[0]
    engine = get_face_engine()
    
    # Real extraction
    embedding = engine.get_embedding(contents)
    if embedding is None:
        raise HTTPException(status_code=400, detail="No face detected in image")
    
    print(f"DEBUG: Enrollment Embedding Shape: {embedding.shape}, Norm: {np.linalg.norm(embedding)}")

    # Encrypt
    encrypted_template = security.encrypt_data(embedding.tobytes())

    # Metadata
    metadata_dict = _parse_json_form_field(metadata, "metadata") or {}
    metadata_dict.update({
        "engine": "insightface_buffalo_l",
        "image_hash": utils.get_image_hash(contents),
        "liveness_checked": liveness_checked,
        "liveness_passed": liveness_passed,
        "frames_received": len(frames_content),
    })

    try:
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

        # Rebuild index to avoid stale/duplicate entries on re-enrollment updates.
        load_faiss_index(db)

        return {
            "success": True,
            "status": "success",
            "message": f"Face enrollment completed for {student.full_name}",
            "student_id": student.id,
            "external_id": student.external_id,
            "liveness_passed": liveness_passed,
            "liveness_checked": liveness_checked,
        }
    except Exception as e:
        db.rollback()
        print(f"Enrollment Error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Database or Engine error: {str(e)}")


@app.post("/api/v1/enroll", status_code=status.HTTP_201_CREATED)
async def enroll_face(
    external_id: Optional[str] = Form(None),
    matric_number: Optional[str] = Form(None),
    metadata: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
    db: Session = Depends(database.get_db),
    settings: models.SystemSettings = Depends(get_settings),
    admin: models.Admin = Depends(get_current_admin),
):
    return await _enroll_face_impl(
        db=db,
        settings=settings,
        matric_number=matric_number,
        external_id=external_id,
        metadata=metadata,
        single_file=None,
        multiple_files=files,
    )


@app.post("/api/v1/enroll/upload", status_code=status.HTTP_201_CREATED)
async def enroll_face_upload(
    matric_number: Optional[str] = Form(None),
    external_id: Optional[str] = Form(None),
    metadata: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    files: List[UploadFile] = File([]),
    db: Session = Depends(database.get_db),
    settings: models.SystemSettings = Depends(get_settings),
    admin: models.Admin = Depends(get_current_admin),
):
    return await _enroll_face_impl(
        db=db,
        settings=settings,
        matric_number=matric_number,
        external_id=external_id,
        metadata=metadata,
        single_file=file,
        multiple_files=files,
    )

# --- Verification & Identification ---

async def process_verification_images(files: List[UploadFile], liveness_enabled: bool) -> (np.ndarray, bool):
    """Helper to handle multiple frames and liveness."""
    frames_content = await _read_and_validate_frames(files)

    # Liveness is only checked when explicitly enabled and enough frames were supplied.
    liveness_passed, _ = _compute_liveness(frames_content, liveness_enabled)
    
    # Get embedding from the first frame
    engine = get_face_engine()
    embedding = engine.get_embedding(frames_content[0])
    
    return embedding, liveness_passed

@app.post("/api/v1/verify/{identifier}", response_model=schemas.StandardResponse)
async def verify_student(
    identifier: str,
    file: UploadFile = File(..., description="Main face image for verification"),
    extra_frames: List[UploadFile] = File([], description="Additional frames for liveness detection"),
    audit_info: Optional[str] = Form(None, description="Optional JSON string for audit metadata"),
    db: Session = Depends(database.get_db),
    settings: models.SystemSettings = Depends(get_settings),
    admin: models.Admin = Depends(get_current_admin)
):
    # Try looking up by internal ID first if it's numeric, otherwise by external_id
    student = None
    if identifier.isdigit():
        student = db.query(models.Student).filter(models.Student.id == int(identifier)).first()
    
    if not student:
        student = db.query(models.Student).filter(models.Student.external_id == identifier).first()
        
    if not identifier or len(identifier.strip()) < 1:
        raise HTTPException(status_code=400, detail="Identifier is required")

    if not student or not student.biometric_enrolled:
        raise HTTPException(status_code=400, detail="Student not found or not enrolled")

    # Combine images for processing
    images = [file] + extra_frames
    
    # 1. Process Images & Liveness
    embedding, liveness_passed = await process_verification_images(images, settings.liveness_enabled)
    
    if embedding is None:
        raise HTTPException(status_code=400, detail="No face detected")
    
    if settings.liveness_enabled and not liveness_passed:
        return schemas.StandardResponse(
            matched=False, student_id=student.id, confidence=0.0,
            mode="1:1", liveness_passed=False, message="Liveness check failed"
        )

    # 2. Get stored template
    stored_enrollment = student.enrollment
    if not stored_enrollment:
        raise HTTPException(status_code=400, detail="Student has no biometric template")

    decrypted_stored_template = security.decrypt_data(stored_enrollment.face_template)
    stored_embedding = np.frombuffer(decrypted_stored_template, dtype=np.float32)

    print(f"DEBUG: Verify Live Embedding Norm: {np.linalg.norm(embedding)}")
    print(f"DEBUG: Verify Stored Embedding Norm: {np.linalg.norm(stored_embedding)}")

    # 3. Match
    confidence = float(np.dot(embedding, stored_embedding)) # Both are pre-normalized
    print(f"DEBUG: Resulting Confidence: {confidence}")
    matched = confidence >= settings.similarity_threshold

    # 4. Log
    audit = _parse_json_form_field(audit_info, "audit_info")

    log = models.VerificationRecord(
        student_id=student.id,
        match_score=confidence,
        is_successful=matched,
        matching_mode="1:1",
        liveness_passed=liveness_passed,
        audit_metadata=audit,
    )
    db.add(log)
    db.commit()

    return schemas.StandardResponse(
        matched=matched,
        student_id=student.id,
        confidence=confidence,
        mode="1:1",
        liveness_passed=liveness_passed,
        message="Match successful" if matched else "Match failed"
    )

@app.post("/api/v1/identify", response_model=schemas.StandardResponse)
async def identify_student(
    file: UploadFile = File(..., description="Face image to identify"),
    extra_frames: List[UploadFile] = File([], description="Optional extra frames for liveness"),
    audit_info: Optional[str] = Form(None, description="Optional JSON string for audit metadata"),
    db: Session = Depends(database.get_db),
    settings: models.SystemSettings = Depends(get_settings),
    admin: models.Admin = Depends(get_current_admin)
):
    # Combine images
    images = [file] + extra_frames
    
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

    audit = _parse_json_form_field(audit_info, "audit_info")

    # 3. Log
    log = models.VerificationRecord(
        student_id=best_match["student_id"],
        match_score=confidence,
        is_successful=matched,
        matching_mode="1:N",
        liveness_passed=liveness_passed,
        audit_metadata={"search_results": results, "audit": audit or {}},
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
