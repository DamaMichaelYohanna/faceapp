from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Float, LargeBinary, Enum, JSON
from sqlalchemy.orm import relationship
from .database import Base
import datetime
import enum
import uuid

class EnrollmentStatus(enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    EXPIRED = "expired"

class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(String, unique=True, index=True) # e.g FT22ACMP0833
    full_name = Column(String) # e.g Dama Michael Yohanna | Surname last
    biometric_enrolled = Column(Boolean, default=False) # Biometric enrolment status
    # establish a one to one relation with the enrolement table and verifications table
    enrollment = relationship("FaceEnrollment", back_populates="student", uselist=False)
    verifications = relationship("VerificationRecord", back_populates="student")

class FaceEnrollment(Base):
    __tablename__ = "face_enrollments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    student_id = Column(Integer, ForeignKey("students.id"), unique=True)
    
    # Encrypted biometric template
    face_template = Column(LargeBinary, nullable=False)
    
    # Hash of the original image for integrity check (optional)
    image_hash = Column(String, nullable=True)
    
    # Secure path to the stored image (if retention policy allows)
    secure_image_uri = Column(String, nullable=True)
    
    status = Column(Enum(EnrollmentStatus), default=EnrollmentStatus.PENDING)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # Audit & Troubleshooting metadata
    # e.g., {"device_id": "CAM-01", "lighting": "good", "vendor": "azure_face_api"}
    metadata_json = Column(JSON, nullable=True)
    
    student = relationship("Student", back_populates="enrollment")

class VerificationRecord(Base):
    __tablename__ = "verification_records"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    student_id = Column(Integer, ForeignKey("students.id"))
    
    match_score = Column(Float)
    is_successful = Column(Boolean)
    
    # 1:1 Matching vs future 1:N Identification
    matching_mode = Column(String, default="1:1")
    
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    
    # Audit context: IP, Device info, Location
    audit_metadata = Column(JSON, nullable=True)
    liveness_passed = Column(Boolean, default=False)
    
    student = relationship("Student", back_populates="verifications")

class SystemSettings(Base):
    __tablename__ = "system_settings"
    
    id = Column(Integer, primary_key=True, default=1)
    matching_mode = Column(String, default="1:1") # "1:1" or "1:N"
    similarity_threshold = Column(Float, default=0.65)
    liveness_enabled = Column(Boolean, default=True)
    max_attempts = Column(Integer, default=3)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class UserRole(enum.Enum):
    ADMIN = "admin"
    CAPTURE_STAFF = "capture_staff"
    VERIFY_STAFF = "verify_staff"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.VERIFY_STAFF, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
