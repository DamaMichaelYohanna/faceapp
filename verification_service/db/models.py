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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class SystemConfig(Base):
    """Master-server credentials. Only one row (id=1)."""
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, default=1)
    server_url = Column(String, nullable=False)
    username = Column(String, nullable=False)
    public_key = Column(Text, nullable=False)
    private_key = Column(Text, nullable=False)
    aes_secret = Column(String, nullable=False)
    is_configured = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# ---------------------------------------------------------------------------
# Cached domain hierarchy
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


# ---------------------------------------------------------------------------
# Biometric data
# ---------------------------------------------------------------------------

class Student(Base):
    """Local student record. external_id = upstream authId (stable key)."""
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(String, unique=True, index=True)   # = upstream authId
    full_name = Column(String)
    biometric_enrolled = Column(Boolean, default=False)

    enrollment = relationship("FaceEnrollment", back_populates="student", uselist=False)
    verifications = relationship("VerificationRecord", back_populates="student")


class FaceEnrollment(Base):
    __tablename__ = "face_enrollments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    student_id = Column(Integer, ForeignKey("students.id"), unique=True)

    face_template = Column(LargeBinary, nullable=False)     # Fernet-encrypted
    image_hash = Column(String, nullable=True)
    status = Column(Enum(EnrollmentStatus), default=EnrollmentStatus.PENDING)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    metadata_json = Column(JSON, nullable=True)

    student = relationship("Student", back_populates="enrollment")


class VerificationRecord(Base):
    __tablename__ = "verification_records"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    student_id = Column(Integer, ForeignKey("students.id"))
    match_score = Column(Float)
    is_successful = Column(Boolean)
    matching_mode = Column(String, default="1:1")
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    audit_metadata = Column(JSON, nullable=True)
    liveness_passed = Column(Boolean, default=False)

    student = relationship("Student", back_populates="verifications")


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, default=1)
    matching_mode = Column(String, default="1:1")
    similarity_threshold = Column(Float, default=0.65)
    liveness_enabled = Column(Boolean, default=True)
    max_attempts = Column(Integer, default=3)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
