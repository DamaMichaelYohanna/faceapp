from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Float, LargeBinary, Enum, JSON, Text
from sqlalchemy.orm import relationship
from .database import Base
import datetime
import enum
import uuid


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EnrollmentStatus(enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    EXPIRED = "expired"


class UploadStatus(enum.Enum):
    PENDING = "pending"       # Captured locally, not yet uploaded to master
    UPLOADING = "uploading"   # In-flight
    UPLOADED = "uploaded"     # Successfully uploaded
    FAILED = "failed"         # Last upload attempt failed


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class SystemConfig(Base):
    """Master-server credentials. Only one row (id=1)."""
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, default=1)
    server_url = Column(String, nullable=False)
    username = Column(String, nullable=False)           # operator username
    public_key = Column(Text, nullable=False)           # RSA public key → Identity header
    private_key = Column(Text, nullable=False)          # RSA private key → Secret header
    aes_secret = Column(String, nullable=False)         # Symmetric key for upload encryption
    is_configured = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# ---------------------------------------------------------------------------
# Cached domain hierarchy (synced from master + domain servers)
# ---------------------------------------------------------------------------

class CachedDomain(Base):
    __tablename__ = "cached_domains"

    id = Column(Integer, primary_key=True, autoincrement=True)
    host = Column(String, unique=True, nullable=False)
    identity = Column(String, nullable=False)
    secret = Column(String, nullable=False)
    synced_at = Column(DateTime, default=datetime.datetime.utcnow)

    departments = relationship("CachedDepartment", back_populates="domain", cascade="all, delete-orphan")
    programme_types = relationship("CachedProgrammeType", back_populates="domain", cascade="all, delete-orphan")


class CachedDepartment(Base):
    __tablename__ = "cached_departments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    upstream_id = Column(Integer, nullable=False)
    domain_id = Column(Integer, ForeignKey("cached_domains.id"), nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=True)

    domain = relationship("CachedDomain", back_populates="departments")


class CachedProgrammeType(Base):
    __tablename__ = "cached_programme_types"

    id = Column(Integer, primary_key=True, autoincrement=True)
    upstream_id = Column(Integer, nullable=False)
    domain_id = Column(Integer, ForeignKey("cached_domains.id"), nullable=False)
    name = Column(String, nullable=False)

    domain = relationship("CachedDomain", back_populates="programme_types")
    levels = relationship("CachedLevel", back_populates="programme_type", cascade="all, delete-orphan")


class CachedLevel(Base):
    __tablename__ = "cached_levels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    upstream_id = Column(Integer, nullable=False)
    programme_type_id = Column(Integer, ForeignKey("cached_programme_types.id"), nullable=False)
    name = Column(String, nullable=False)

    programme_type = relationship("CachedProgrammeType", back_populates="levels")


class CachedStudent(Base):
    """
    Students fetched from domain servers and cached locally for offline access.
    auth_id is the stable upstream key; user_id is the human-readable matric/staff number.
    """
    __tablename__ = "cached_students"

    id = Column(Integer, primary_key=True, autoincrement=True)
    auth_id = Column(String, unique=True, nullable=False)   # stable upstream authId
    user_id = Column(String, nullable=False)                # matric / staff number
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)               # STUDENT, STAFF, etc.
    domain_id = Column(Integer, ForeignKey("cached_domains.id"), nullable=True)
    department_upstream_id = Column(Integer, nullable=True)
    last_seen_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# ---------------------------------------------------------------------------
# Biometric data
# ---------------------------------------------------------------------------

class Student(Base):
    """Local student record linked to a face enrollment. external_id = upstream authId."""
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(String, unique=True, index=True)   # = upstream authId
    full_name = Column(String)
    biometric_enrolled = Column(Boolean, default=False)

    enrollment = relationship("FaceEnrollment", back_populates="student", uselist=False)


class FaceEnrollment(Base):
    __tablename__ = "face_enrollments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    student_id = Column(Integer, ForeignKey("students.id"), unique=True)

    # Encrypted (Fernet) biometric template — the local copy
    face_template = Column(LargeBinary, nullable=False)
    image_hash = Column(String, nullable=True)

    status = Column(Enum(EnrollmentStatus), default=EnrollmentStatus.PENDING)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    metadata_json = Column(JSON, nullable=True)

    # --- Upstream sync tracking ---
    upload_status = Column(Enum(UploadStatus), default=UploadStatus.PENDING)
    uploaded_at = Column(DateTime, nullable=True)
    upload_error = Column(Text, nullable=True)    # last error message if FAILED

    student = relationship("Student", back_populates="enrollment")


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, default=1)
    liveness_enabled = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
