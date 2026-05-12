from pydantic import BaseModel, HttpUrl, validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum


class EnrollmentStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    EXPIRED = "expired"


class UploadStatus(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# System Config
# ---------------------------------------------------------------------------

class SystemConfigCreate(BaseModel):
    server_url: str
    username: str
    public_key: str
    private_key: str
    aes_secret: str

    @validator("server_url")
    def strip_trailing_slash(cls, v):
        return v.rstrip("/")


class SystemConfigResponse(BaseModel):
    server_url: str
    username: str
    public_key: str          # shown (used to register with master)
    private_key_hint: str    # last 8 chars only — never expose full key
    is_configured: bool
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Cached domain hierarchy
# ---------------------------------------------------------------------------

class DomainResponse(BaseModel):
    id: int
    host: str
    synced_at: datetime

    class Config:
        from_attributes = True


class DepartmentResponse(BaseModel):
    id: int
    upstream_id: int
    name: str
    code: Optional[str] = None

    class Config:
        from_attributes = True


class ProgrammeTypeResponse(BaseModel):
    id: int
    upstream_id: int
    name: str

    class Config:
        from_attributes = True


class LevelResponse(BaseModel):
    id: int
    upstream_id: int
    name: str

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Upstream user (live from domain API)
# ---------------------------------------------------------------------------

class UpstreamUser(BaseModel):
    auth_id: str
    user_id: str
    name: str
    category: str


# ---------------------------------------------------------------------------
# Student (local)
# ---------------------------------------------------------------------------

class Student(BaseModel):
    id: int
    external_id: str
    full_name: str
    biometric_enrolled: bool

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

class EnrollResponse(BaseModel):
    success: bool
    status: str
    message: str
    student_id: int
    external_id: str
    liveness_passed: bool
    liveness_checked: bool
    upload_status: UploadStatus


# ---------------------------------------------------------------------------
# Sync / Upload
# ---------------------------------------------------------------------------

class SyncStatusResponse(BaseModel):
    pending: int
    uploaded: int
    failed: int


class SyncResultResponse(BaseModel):
    attempted: int
    succeeded: int
    failed: int
    errors: List[str]


# ---------------------------------------------------------------------------
# System Settings
# ---------------------------------------------------------------------------

class SystemSettingsBase(BaseModel):
    liveness_enabled: bool = True


class SystemSettingsUpdate(SystemSettingsBase):
    pass


class SystemSettings(SystemSettingsBase):
    id: int
    updated_at: datetime

    class Config:
        from_attributes = True
