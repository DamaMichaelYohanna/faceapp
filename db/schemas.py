from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum

class EnrollmentStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    EXPIRED = "expired"

class StudentBase(BaseModel):
    external_id: str
    full_name: str

class StudentCreate(StudentBase):
    pass

class Student(StudentBase):
    id: int
    biometric_enrolled: bool

    class Config:
        from_attributes = True

class FaceEnrollmentBase(BaseModel):
    student_id: int
    metadata_json: Optional[Dict[str, Any]] = None

class FaceEnrollmentCreate(FaceEnrollmentBase):
    pass

class FaceEnrollment(FaceEnrollmentBase):
    id: str
    status: EnrollmentStatus
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class StandardResponse(BaseModel):
    matched: bool
    student_id: Optional[int] = None
    full_name: Optional[str] = None
    confidence: float
    threshold: float = 0.0          # The threshold that was active at decision time
    decision_reason: str = ""       # Human-readable decision explanation
    mode: str
    liveness_passed: bool
    message: str

class VerificationLog(BaseModel):
    id: str
    student_id: int
    match_score: float
    is_successful: bool
    matching_mode: str
    timestamp: datetime
    audit_metadata: Optional[Dict[str, Any]] = None
    liveness_passed: bool

    class Config:
        from_attributes = True

class EnrollmentStatusResponse(BaseModel):
    student_id: int
    is_enrolled: bool
    status: Optional[EnrollmentStatus]
    last_updated: Optional[datetime]

class SystemSettingsBase(BaseModel):
    matching_mode: str = "1:1"
    similarity_threshold: float = 0.65
    liveness_enabled: bool = True
    max_attempts: int = 3

class SystemSettingsUpdate(SystemSettingsBase):
    pass

class SystemSettings(SystemSettingsBase):
    id: int
    updated_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

class UserRole(str, Enum):
    ADMIN = "admin"
    CAPTURE_STAFF = "capture_staff"
    VERIFY_STAFF = "verify_staff"

class UserBase(BaseModel):
    username: str
    role: UserRole
    is_active: bool = True

class UserCreate(UserBase):
    password: str

class User(UserBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)
