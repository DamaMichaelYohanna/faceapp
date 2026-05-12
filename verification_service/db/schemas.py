from pydantic import BaseModel, validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum


class EnrollmentStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    EXPIRED = "expired"


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
    public_key: str
    private_key_hint: str
    is_configured: bool
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Domain hierarchy
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


class UpstreamUser(BaseModel):
    auth_id: str
    user_id: str
    name: str
    category: str


# ---------------------------------------------------------------------------
# Verification responses
# ---------------------------------------------------------------------------

class StandardResponse(BaseModel):
    matched: bool
    student_id: Optional[int] = None
    external_id: Optional[str] = None    # upstream authId
    full_name: Optional[str] = None
    confidence: float
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


# ---------------------------------------------------------------------------
# System Settings
# ---------------------------------------------------------------------------

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
