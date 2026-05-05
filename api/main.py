import os
import sys
import asyncio
import base64
import datetime
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple
import json
import traceback

from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, status, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
import numpy as np

# Adjust path to import from parent directory if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import models, schemas, database
from core.face_engine import get_face_engine, get_fast_face_detector
from core.liveness import LivenessDetector
from core.faiss_service import get_faiss_service
import security, utils


# Allow all origins so the HTML frontend (opened as file://) can reach the API.
# Restrict origins to your domain in production.
_CORS_KWARGS = dict(
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        admin = db.query(models.User).filter(models.User.role == models.UserRole.ADMIN).first()
        if not admin:
            default_admin = models.User(
                username=os.getenv("ADMIN_USERNAME", "admin"),
                hashed_password=security.get_password_hash(os.getenv("ADMIN_PASSWORD", "admin123")),
                role=models.UserRole.ADMIN
            )
            db.add(default_admin)
            
        db.commit()
        
        # Warm up Face Engine + Fast Detector for real-time WebSocket liveness
        get_face_engine()
        get_fast_face_detector()
        
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
app.add_middleware(CORSMiddleware, **_CORS_KWARGS)

# =============================================================================
# SDK / PROVIDER INTEGRATION STUBS
# To swap in a commercial SDK (e.g. AWS Rekognition, Azure Face, NEC NeoFace,
# Innovatrics, or Aware), replace the relevant functions below with the
# provider's client calls. The rest of the pipeline (liveness, audit logging,
# 1:1 vs 1:N routing, FAISS index) remains unchanged.
#
# Required interface for any provider adapter:
#   get_embedding(image_bytes: bytes) -> np.ndarray   # L2-normalised 512-d vector
#   has_face(image_bytes: bytes) -> bool
#   compare(emb_a, emb_b) -> float                    # cosine similarity 0–1
#
# Decision thresholds (configurable via SystemSettings.similarity_threshold):
#   >= 0.75  High confidence match
#   >= 0.65  Standard match  (default)
#   < 0.65   No-match
#   < 0.30   Definitive reject
#
# 1:1 Verification  → POST /api/v1/verify/{identifier}  (require identifier)
# 1:N Identification → POST /api/v1/identify            (no identifier needed)
# Both routes log to VerificationRecord with matching_mode='1:1' / '1:N'
# =============================================================================

MIN_ACTIVE_LIVENESS_FRAMES = 3
MIN_FACE_CONSISTENCY_SIMILARITY = 0.45
# --- Dependency ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(database.get_db)):
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
        
    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user

def check_role(allowed_roles: List[models.UserRole]):
    def role_checker(current_user: models.User = Depends(get_current_user)):
        # Admin has unlimited rights
        if current_user.role == models.UserRole.ADMIN:
            return current_user
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation not permitted"
            )
        return current_user
    return role_checker

require_capture = Depends(check_role([models.UserRole.CAPTURE_STAFF]))
require_verify = Depends(check_role([models.UserRole.VERIFY_STAFF, models.UserRole.CAPTURE_STAFF]))
require_admin = Depends(check_role([]))

def get_settings(db: Session = Depends(database.get_db)):
    return db.query(models.SystemSettings).first()

# --- Public & Auth API ---

@app.post("/api/v1/auth/login", response_model=schemas.Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(database.get_db)):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
        
    access_token = security.create_access_token(
        data={"sub": user.username}
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/v1/auth/me", response_model=schemas.User)
def get_me(current_user: models.User = Depends(get_current_user)):
    """Return the authenticated user's profile including role."""
    return current_user


@app.post("/api/v1/auth/change-password")
def change_password(
    payload: schemas.ChangePasswordRequest,
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not security.verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="New password must be different from current password")

    current_user.hashed_password = security.get_password_hash(payload.new_password)
    db.add(current_user)
    db.commit()
    return {"message": "Password updated successfully"}

@app.post("/api/v1/users/", response_model=schemas.User, status_code=status.HTTP_201_CREATED)
def create_user(
    user: schemas.UserCreate, 
    db: Session = Depends(database.get_db),
    current_user: models.User = require_admin
):
    # Only allow creating capture_staff and verify_staff, not other admins
    if user.role == models.UserRole.ADMIN:
        raise HTTPException(status_code=400, detail="Cannot create users with the admin role.")

    db_user = db.query(models.User).filter(models.User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = security.get_password_hash(user.password)
    new_user = models.User(
        username=user.username,
        hashed_password=hashed_password,
        role=user.role,
        is_active=user.is_active
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    if isinstance(exc, StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    
    print(f"CRITICAL ERROR: {exc}")
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"}
    )

# --- Liveness WebSocket ---

_WS_FRAME_BUFFER_MAX = 5  # rolling window size

@app.websocket("/ws/liveness")
async def liveness_websocket(websocket: WebSocket, token: Optional[str] = None):
    """Real-time liveness analysis. Clients stream base64 frames; server responds with liveness status."""
    # Optional JWT auth via query param ?token=<jwt>
    if token:
        try:
            payload = jwt.decode(token, security.JWT_SECRET_KEY, algorithms=[security.ALGORITHM])
            username: str = payload.get("sub", "")
            if not username:
                await websocket.close(code=4001)
                return
            db = database.SessionLocal()
            user = db.query(models.User).filter(models.User.username == username).first()
            db.close()
            if not user or not user.is_active:
                await websocket.close(code=4001)
                return
        except JWTError:
            await websocket.close(code=4001)
            return

    await websocket.accept()
    frame_buffer: List[bytes] = []

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            if msg.get("type") != "analyze_frame":
                continue

            frame_b64: str = msg.get("payload", {}).get("frame", "")
            if not frame_b64:
                continue

            # Strip data-URL prefix (data:image/jpeg;base64,...)
            if "," in frame_b64:
                frame_b64 = frame_b64.split(",", 1)[1]

            try:
                frame_bytes = base64.b64decode(frame_b64)
            except Exception:
                await websocket.send_text(json.dumps({"type": "error", "message": "Invalid frame encoding"}))
                continue

            # Fast face detection (160x160 detection-only, no embedding) for real-time response
            face_in_frame = get_fast_face_detector().has_face(frame_bytes)

            if face_in_frame:
                frame_buffer.append(frame_bytes)
                if len(frame_buffer) > _WS_FRAME_BUFFER_MAX:
                    frame_buffer.pop(0)
            else:
                # Face left — clear stale buffer so liveness must re-prove from scratch
                frame_buffer.clear()

            count = len(frame_buffer)
            liveness_passed = False
            has_motion = False

            if face_in_frame and count >= 2:
                analysis = LivenessDetector.analyze_frames(frame_buffer)
                has_motion = analysis["has_motion"]
                if count >= MIN_ACTIVE_LIVENESS_FRAMES:
                    liveness_passed = analysis["liveness_passed"]

            await websocket.send_text(json.dumps({
                "type": "liveness_status",
                "payload": {
                    "liveness_passed": liveness_passed,
                    "count": count,
                    "has_motion": has_motion,
                    "face_in_frame": face_in_frame,
                }
            }))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WS liveness error: {e}")
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": "Internal error"}))
        except Exception:
            pass


# --- Liveness Check (standalone, used by frontend pre-enrollment) ---

@app.post("/api/v1/liveness/check")
async def check_liveness(
    files: List[UploadFile] = File(..., description="3-5 face frames for liveness analysis"),
    current_user: models.User = Depends(get_current_user),
):
    if len(files) < MIN_ACTIVE_LIVENESS_FRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"At least {MIN_ACTIVE_LIVENESS_FRAMES} frames required for liveness check."
        )
    frames_content = await _read_and_validate_frames(files)
    passed = LivenessDetector.check_liveness(frames_content)
    return {"liveness_passed": passed, "frames_analyzed": len(frames_content)}


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
    "132132": {"full_name": "Test Student 132132"},
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


async def _extract_embedding_or_http_error(engine: Any, image_bytes: bytes, frame_label: str) -> Optional[np.ndarray]:
    """Normalize face-engine image failures into actionable client errors. Runs in thread pool."""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, engine.get_embedding, image_bytes)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not process the {frame_label}. Retake and ensure the face is clearly visible.",
        ) from exc


def _validate_face_consistency(engine: Any, frames_content: List[bytes], main_embedding: np.ndarray) -> None:
    """Ensure a face is detectable in at least one liveness frame (fast detection-only check)."""
    liveness_frames = frames_content[1:]
    if not liveness_frames:
        raise HTTPException(status_code=400, detail="Missing liveness frames for face consistency check.")

    fast_det = get_fast_face_detector()
    has_any_face = any(fast_det.has_face(frame) for frame in liveness_frames)
    if not has_any_face:
        raise HTTPException(
            status_code=400,
            detail="Face not detected in liveness frames. Keep the face inside the guide and retry.",
        )

# --- Student Management ---

@app.get("/api/v1/students/", response_model=List[schemas.Student])
def list_students(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(database.get_db),
    current_user: models.User = Depends(get_current_user)
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

    try:
        # 2. Resolve student record
        # Prefer local DB first so enrollment still works if the external service is down
        # for records that already exist in this system.
        student = db.query(models.Student).filter(models.Student.external_id == matric).first()
        if not student:
            try:
                student_data = await fetch_student_from_external(matric)
            except HTTPException as exc:
                if exc.status_code >= 500:
                    raise HTTPException(
                        status_code=503,
                        detail="Student registry service is temporarily unavailable. Retry shortly.",
                    )
                raise

            # Create student locally if they exist in main storage but not here
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

        # Real extraction — runs in thread pool to avoid blocking the event loop
        embedding = await _extract_embedding_or_http_error(engine, contents, "captured image")
        if embedding is None:
            raise HTTPException(status_code=400, detail="No face detected in image")

        print(f"DEBUG: Enrollment Embedding Shape: {embedding.shape}, Norm: {np.linalg.norm(embedding)}")

        # Ensure the same person remained in frame between liveness sequence and final capture.
        if settings.liveness_enabled:
            _validate_face_consistency(engine, frames_content, embedding)

        # Encrypt
        encrypted_template = security.encrypt_data(embedding.tobytes())

        # Metadata
        metadata_dict = _parse_json_form_field(metadata, "metadata") or {}
        thumb = utils.make_thumbnail(contents)
        metadata_dict.update({
            "engine": "insightface_buffalo_l",
            "image_hash": utils.get_image_hash(contents),
            "liveness_checked": liveness_checked,
            "liveness_passed": liveness_passed,
            "frames_received": len(frames_content),
        })
        if thumb:
            metadata_dict["thumbnail"] = thumb
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Enrollment Preprocessing Error: {e}")
        traceback.print_exc()
        raise HTTPException(
            status_code=503,
            detail="Face processing engine unavailable. Retry shortly.",
        )

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
        # Isolated so a FAISS reload failure never poisons the successful enrollment response.
        try:
            load_faiss_index(db)
        except Exception as faiss_err:
            print(f"FAISS reload warning (enrollment was saved successfully): {faiss_err}")
            traceback.print_exc()

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
    current_user: models.User = require_capture,
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
    current_user: models.User = require_capture,
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
    
    # Get embedding from the first frame — run in thread pool to avoid blocking the event loop
    engine = get_face_engine()
    loop = asyncio.get_event_loop()
    embedding = await loop.run_in_executor(None, engine.get_embedding, frames_content[0])

    return embedding, liveness_passed

@app.post("/api/v1/verify/{identifier:path}", response_model=schemas.StandardResponse)
async def verify_student(
    identifier: str,
    file: UploadFile = File(..., description="Main face image for verification"),
    extra_frames: List[UploadFile] = File([], description="Additional frames for liveness detection"),
    audit_info: Optional[str] = Form(None, description="Optional JSON string for audit metadata"),
    db: Session = Depends(database.get_db),
    settings: models.SystemSettings = Depends(get_settings),
    current_user: models.User = require_verify
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

    # 4. Log — include operator identity and decision context in audit metadata
    audit = _parse_json_form_field(audit_info, "audit_info") or {}
    audit.update({
        "operator": current_user.username,
        "operator_role": current_user.role.value,
        "threshold": settings.similarity_threshold,
        "raw_confidence": round(confidence, 6),
        "decision_reason": (
            "liveness_failed" if not liveness_passed
            else ("above_threshold" if matched else "below_threshold")
        ),
    })

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

    decision_reason = (
        f"Liveness check failed — no match recorded"
        if not liveness_passed
        else (f"Confidence {confidence:.2%} ≥ threshold {settings.similarity_threshold:.2%} — match confirmed"
              if matched
              else f"Confidence {confidence:.2%} < threshold {settings.similarity_threshold:.2%} — match rejected")
    )

    return schemas.StandardResponse(
        matched=matched,
        student_id=student.id,
        full_name=student.full_name,
        confidence=confidence,
        threshold=settings.similarity_threshold,
        decision_reason=decision_reason,
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
    current_user: models.User = require_verify
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

    audit = _parse_json_form_field(audit_info, "audit_info") or {}
    audit.update({
        "operator": current_user.username,
        "operator_role": current_user.role.value,
        "threshold": settings.similarity_threshold,
        "raw_confidence": round(confidence, 6),
        "search_results": results,
        "decision_reason": (
            "liveness_failed" if not liveness_passed
            else ("above_threshold" if matched else "below_threshold")
        ),
    })

    # 3. Log
    log = models.VerificationRecord(
        student_id=best_match["student_id"],
        match_score=confidence,
        is_successful=matched,
        matching_mode="1:N",
        liveness_passed=liveness_passed,
        audit_metadata=audit,
    )
    db.add(log)
    db.commit()

    # Resolve student name for 1:N result
    matched_student = None
    if matched and student_id:
        matched_student = db.query(models.Student).filter(models.Student.id == student_id).first()

    decision_reason = (
        f"Liveness check failed — no match recorded"
        if not liveness_passed
        else (f"Confidence {confidence:.2%} ≥ threshold {settings.similarity_threshold:.2%} — identity confirmed"
              if matched
              else f"Confidence {confidence:.2%} < threshold {settings.similarity_threshold:.2%} — identity unconfirmed")
    )

    return schemas.StandardResponse(
        matched=matched,
        student_id=student_id,
        full_name=matched_student.full_name if matched_student else None,
        confidence=confidence,
        threshold=settings.similarity_threshold,
        decision_reason=decision_reason,
        mode="1:N",
        liveness_passed=liveness_passed,
        message="Identified" if matched else "Identity not confirmed"
    )

# --- Admin API ---

@app.get("/admin/settings", response_model=schemas.SystemSettings)
def get_admin_settings(
    settings: models.SystemSettings = Depends(get_settings),
    current_user: models.User = require_admin
):
    return settings

@app.put("/admin/settings", response_model=schemas.SystemSettings)
def update_admin_settings(
    payload: schemas.SystemSettingsUpdate, 
    db: Session = Depends(database.get_db),
    current_user: models.User = require_admin
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
    current_user: models.User = require_admin
):
    load_faiss_index(db)
    return {"message": "FAISS index reloaded successfully"}

@app.get("/admin/stats")
def get_admin_stats(
    db: Session = Depends(database.get_db),
    current_user: models.User = require_admin
):
    """Dashboard analytics summary."""
    total_students = db.query(models.Student).count()
    enrolled_students = db.query(models.Student).filter(models.Student.biometric_enrolled == True).count()
    total_verifications = db.query(models.VerificationRecord).count()
    successful_verifications = db.query(models.VerificationRecord).filter(models.VerificationRecord.is_successful == True).count()
    today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    verifications_today = db.query(models.VerificationRecord).filter(models.VerificationRecord.timestamp >= today).count()
    active_enrollments = db.query(models.FaceEnrollment).filter(models.FaceEnrollment.status == models.EnrollmentStatus.ACTIVE).count()
    total_staff = db.query(models.User).filter(models.User.role != models.UserRole.ADMIN).count()
    success_rate = round((successful_verifications / total_verifications * 100), 1) if total_verifications > 0 else 0.0
    return {
        "total_students": total_students,
        "enrolled_students": enrolled_students,
        "unenrolled_students": total_students - enrolled_students,
        "active_enrollments": active_enrollments,
        "total_verifications": total_verifications,
        "successful_verifications": successful_verifications,
        "verifications_today": verifications_today,
        "success_rate": success_rate,
        "total_staff": total_staff,
    }


@app.get("/admin/enrollments")
def list_admin_enrollments(
    skip: int = 0,
    limit: int = 50,
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    db: Session = Depends(database.get_db),
    current_user: models.User = require_admin
):
    """List all enrollments with student info for admin management."""
    query = db.query(models.FaceEnrollment).join(models.Student)
    if status_filter:
        try:
            s = models.EnrollmentStatus(status_filter)
            query = query.filter(models.FaceEnrollment.status == s)
        except ValueError:
            pass
    if search:
        query = query.filter(
            models.Student.full_name.ilike(f"%{search}%") |
            models.Student.external_id.ilike(f"%{search}%")
        )
    total = query.count()
    enrollments = query.order_by(models.FaceEnrollment.updated_at.desc()).offset(skip).limit(limit).all()
    results = []
    for e in enrollments:
        results.append({
            "id": e.id,
            "student_id": e.student_id,
            "external_id": e.student.external_id,
            "full_name": e.student.full_name,
            "status": e.status.value,
            "created_at": e.created_at.isoformat(),
            "updated_at": e.updated_at.isoformat(),
            "metadata_json": e.metadata_json,
        })
    return {"total": total, "items": results}


@app.put("/admin/enrollments/{enrollment_id}/status")
def update_enrollment_status(
    enrollment_id: str,
    payload: dict,
    db: Session = Depends(database.get_db),
    current_user: models.User = require_admin
):
    """Update enrollment status (active/rejected/expired)."""
    enrollment = db.query(models.FaceEnrollment).filter(models.FaceEnrollment.id == enrollment_id).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    new_status = payload.get("status")
    try:
        enrollment.status = models.EnrollmentStatus(new_status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")
    # Keep biometric_enrolled flag in sync with active status
    enrollment.student.biometric_enrolled = (enrollment.status == models.EnrollmentStatus.ACTIVE)
    db.commit()
    try:
        load_faiss_index(db)
    except Exception as err:
        print(f"FAISS reload warning after status update: {err}")
    return {"success": True, "id": enrollment_id, "status": enrollment.status.value}


@app.delete("/admin/enrollments/{enrollment_id}", status_code=204)
def delete_enrollment(
    enrollment_id: str,
    db: Session = Depends(database.get_db),
    current_user: models.User = require_admin
):
    """Permanently delete an enrollment record."""
    enrollment = db.query(models.FaceEnrollment).filter(models.FaceEnrollment.id == enrollment_id).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    enrollment.student.biometric_enrolled = False
    db.delete(enrollment)
    db.commit()
    try:
        load_faiss_index(db)
    except Exception as err:
        print(f"FAISS reload warning after delete: {err}")


@app.get("/admin/users")
def list_admin_users(
    db: Session = Depends(database.get_db),
    current_user: models.User = require_admin
):
    """List all staff users."""
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role.value,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat(),
        }
        for u in users
    ]


@app.put("/admin/users/{user_id}")
def update_admin_user(
    user_id: int,
    payload: dict,
    db: Session = Depends(database.get_db),
    current_user: models.User = require_admin
):
    """Toggle user active status or update role."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot modify your own account here")
    if "is_active" in payload:
        user.is_active = bool(payload["is_active"])
    db.commit()
    return {"success": True, "id": user.id, "username": user.username, "is_active": user.is_active}


@app.delete("/admin/users/{user_id}", status_code=204)
def delete_admin_user(
    user_id: int,
    db: Session = Depends(database.get_db),
    current_user: models.User = require_admin
):
    """Delete a staff user account."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    if user.role == models.UserRole.ADMIN:
        raise HTTPException(status_code=400, detail="Cannot delete admin accounts")
    db.delete(user)
    db.commit()


@app.get("/admin/verifications/stats")
def get_verification_stats(
    search: Optional[str] = None,
    mode_filter: Optional[str] = None,
    result_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(database.get_db),
    current_user: models.User = require_admin
):
    """Aggregate analytics for the audit panel — respects same filters as the list endpoint."""
    query = db.query(models.VerificationRecord).join(models.Student, isouter=True)

    if mode_filter in ("1:1", "1:N"):
        query = query.filter(models.VerificationRecord.matching_mode == mode_filter)
    if result_filter == "success":
        query = query.filter(models.VerificationRecord.is_successful == True)
    elif result_filter == "fail":
        query = query.filter(models.VerificationRecord.is_successful == False)
    if search:
        query = query.filter(
            models.Student.full_name.ilike(f"%{search}%") |
            models.Student.external_id.ilike(f"%{search}%")
        )
    if date_from:
        try:
            query = query.filter(models.VerificationRecord.timestamp >= datetime.datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(models.VerificationRecord.timestamp <= datetime.datetime.fromisoformat(date_to))
        except ValueError:
            pass

    records = query.all()
    total = len(records)

    if total == 0:
        return {
            "total": 0, "matched": 0, "failed": 0, "match_rate": 0.0,
            "avg_confidence": 0.0, "liveness_pass_rate": 0.0,
            "mode_breakdown": {}, "top_operators": [], "score_buckets": [0] * 10,
        }

    scores = [r.match_score for r in records]
    matched = sum(1 for r in records if r.is_successful)
    liveness_passed_count = sum(1 for r in records if r.liveness_passed)

    # Build 10 score buckets: 0-10%, 10-20%, ..., 90-100%
    buckets = [0] * 10
    for s in scores:
        buckets[min(int(s * 10), 9)] += 1

    # Mode breakdown
    mode_breakdown: dict = {}
    for r in records:
        mode_breakdown[r.matching_mode] = mode_breakdown.get(r.matching_mode, 0) + 1

    # Top operators from audit_metadata
    op_counts: dict = {}
    for r in records:
        op = (r.audit_metadata or {}).get("operator", "")
        if op:
            op_counts[op] = op_counts.get(op, 0) + 1
    top_ops = sorted(op_counts.items(), key=lambda x: -x[1])[:5]

    return {
        "total": total,
        "matched": matched,
        "failed": total - matched,
        "match_rate": round(matched / total * 100, 1),
        "avg_confidence": round(sum(scores) / total * 100, 1),
        "liveness_pass_rate": round(liveness_passed_count / total * 100, 1),
        "mode_breakdown": mode_breakdown,
        "top_operators": [{"operator": k, "count": v} for k, v in top_ops],
        "score_buckets": buckets,
    }


@app.get("/admin/verifications")
def list_admin_verifications(
    skip: int = 0,
    limit: int = 50,
    search: Optional[str] = None,
    mode_filter: Optional[str] = None,       # '1:1' or '1:N'
    result_filter: Optional[str] = None,     # 'success' or 'fail'
    date_from: Optional[str] = None,         # ISO date string
    date_to: Optional[str] = None,
    db: Session = Depends(database.get_db),
    current_user: models.User = require_admin
):
    """Paginated, filterable verification audit log."""
    query = db.query(models.VerificationRecord).join(models.Student, isouter=True)

    if mode_filter in ("1:1", "1:N"):
        query = query.filter(models.VerificationRecord.matching_mode == mode_filter)
    if result_filter == "success":
        query = query.filter(models.VerificationRecord.is_successful == True)
    elif result_filter == "fail":
        query = query.filter(models.VerificationRecord.is_successful == False)
    if search:
        query = query.filter(
            models.Student.full_name.ilike(f"%{search}%") |
            models.Student.external_id.ilike(f"%{search}%")
        )
    if date_from:
        try:
            query = query.filter(models.VerificationRecord.timestamp >= datetime.datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(models.VerificationRecord.timestamp <= datetime.datetime.fromisoformat(date_to))
        except ValueError:
            pass

    total = query.count()
    records = query.order_by(models.VerificationRecord.timestamp.desc()).offset(skip).limit(limit).all()
    results = []
    for r in records:
        audit = r.audit_metadata or {}
        results.append({
            "id": r.id,
            "student_id": r.student_id,
            "full_name": r.student.full_name if r.student else "Unknown",
            "external_id": r.student.external_id if r.student else "",
            "match_score": round(r.match_score * 100, 1),
            "raw_confidence": round(r.match_score, 6),
            "is_successful": r.is_successful,
            "liveness_passed": r.liveness_passed,
            "matching_mode": r.matching_mode,
            "timestamp": r.timestamp.isoformat(),
            "operator": audit.get("operator", ""),
            "operator_role": audit.get("operator_role", ""),
            "threshold": audit.get("threshold", ""),
            "decision_reason": audit.get("decision_reason", ""),
            "audit_metadata": audit,
        })
    return {"total": total, "items": results}


@app.get("/health")
def health_check():
    return {"status": "healthy", "engine": "loaded", "faiss_size": get_faiss_service().index.ntotal}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
