# Face Biometric Enrollment and Verification API

## Overview
This project provides a production-ready FastAPI backend for student face biometric management. It supports secure enrollment, 1:1 verification, and 1:N identification (search) using state-of-the-art deep learning models and high-performance similarity search.

The system is designed for high-security environments, ensuring that biometric templates are encrypted at rest and authentication attempts are audited with liveness detection.

## Core Features
- **Real Biometric Embeddings**: Utilizes the InsightFace (ArcFace) model to extract 512-dimensional feature vectors.
- **1:1 Verification**: Authenticates a specific student by comparing a live capture against their stored template.
- **1:N Identification**: Searches a database of enrolled students to find a match for an unknown face using FAISS (Facebook AI Similarity Search).
- **Liveness Detection**: Implements motion and texture analysis over multiple frames to prevent spoofing via photos or digital screens.
- **Admin Settings**: A dynamic configuration system allows administrators to adjust similarity thresholds and toggle security features without downtime.
- **Security**: Biometric templates are encrypted using Fernet symmetric encryption before being persisted to the database.

## Technical Stack
- **Framework**: FastAPI (Python)
- **AI Engine**: InsightFace (buffalo_l)
- **Search Engine**: FAISS (IndexFlatIP)
- **Database**: SQLite with SQLAlchemy ORM
- **Processing**: NumPy, OpenCV, ONNX Runtime
- **Security**: Cryptography (Fernet)

## Project Structure
- `api/`: Contains the FastAPI application logic and route definitions.
- `core/`: Core modules for face processing, FAISS management, and liveness detection.
- `db/`: Database configuration, SQLAlchemy models, and Pydantic schemas.
- `security.py`: Logic for data encryption and authentication.
- `utils.py`: General utility functions and image validation helpers.

## Installation

### Prerequisites
- Python 3.9+
- C++ Build Tools (required for certain AI libraries)

### Setup
1. Clone the repository:
   ```bash
   git clone https://github.com/DamaMichaelYohanna/faceapp.git
   cd faceapp
   ```

2. Install dependencies:
   ```bash
   pip install numpy
   pip install -r requirements.txt
   ```
   *Note: Installing numpy first is recommended to ensure smooth installation of the InsightFace library.*

3. Environment Configuration:
   Create a `.env` file in the root directory:
   ```env
   DATABASE_URL=sqlite:///./biometric.db
   BIOMETRIC_SECRET_KEY=your_generated_fernet_key
   ```

## Troubleshooting: Installation Errors

### Error: "Microsoft Visual C++ 14.0 or greater is required"
If you encounter this error while installing `insightface`, it is because the library is attempting to compile C++ extensions but lacks the necessary build tools or pre-compiled binaries for your Python version.

#### Solution 1: Use a Stable Python Version (Recommended)
This project is most stable on **Python 3.11 or 3.12**.
- AI/ML libraries like InsightFace often lack ready-to-use binaries for "bleeding edge" versions like Python 3.13.
- By using Python 3.11 or 3.12, `pip` will download pre-built "wheels," bypassing the need for C++ compilation entirely.

#### Solution 2: Install Microsoft C++ Build Tools
If you must use a newer Python version:
1. Download the [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/).
2. In the installer, select the **"Desktop development with C++"** workload.
3. Ensure **"MSVC v14x"** and **"Windows 10/11 SDK"** are selected.
4. Restart your terminal and retry the installation.

## Usage

### Running the Server
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

## API Documentation

The Face Biometric API follows RESTful principles and uses JSON for all request and response bodies (except for file uploads).

### Base URL
`http://<server-ip>:8000`

---

### 1. Student Management

#### **Create Student**
`POST /api/v1/students/`

Register a new student profile. This must be done before biometric enrollment.

| Field | Type | Description |
| :--- | :--- | :--- |
| `external_id` | `string` | Unique identifier from your external system (e.g., Reg Number). |
| `full_name` | `string` | The student's full legal name. |

**Example Request:**
```json
{
  "external_id": "STU-2024-001",
  "full_name": "Michael Dama"
}
```

---

#### **List Students**
`GET /api/v1/students/`

Retrieve a list of all registered students with pagination.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `skip` | `integer` | `0` | Number of records to skip. |
| `limit` | `integer` | `100` | Maximum number of records to return. |

---

### 2. Biometric Enrollment

#### **Enroll Face**
`POST /api/v1/enroll/upload`

Processes a face image, extracts a 512D embedding, encrypts it, and stores it as the student's biometric template.

**Content-Type:** `multipart/form-data`

| Field | Type | Description |
| :--- | :--- | :--- |
| `student_id` | `integer` | Internal DB ID of the student (not `external_id`). |
| `file` | `file` | A high-quality JPG or PNG image containing a clear, forward-facing face. |
| `metadata` | `string` | (Optional) JSON string for custom data (e.g., `{"device": "iPhone 15"}`). |

---

### 3. Verification & Identification

#### **Verify Student (1:1)**
`POST /api/v1/verify/{student_id}`

Compares live capture frames against the stored template for a specific student.

**Content-Type:** `multipart/form-data`

| Field | Type | Description |
| :--- | :--- | :--- |
| `student_id` | `integer` | Path parameter. The student's internal ID. |
| `images` | `files[]` | One or more images (frames). If multiple are sent, liveness detection is performed. |
| `audit_info` | `string` | (Optional) JSON string for audit trails (e.g., `{"location": "Exam Hall A"}`). |

**Standard Response Schema:**
```json
{
  "matched": true,         // Boolean: True if score >= threshold
  "student_id": 1,         // Integer: The verified student's ID
  "confidence": 0.824,     // Float: Cosine similarity score (0.0 to 1.0)
  "mode": "1:1",           // String: Matching mode used
  "liveness_passed": true, // Boolean: Result of anti-spoofing check
  "message": "Match successful"
}
```

---

#### **Identify Student (1:N)**
`POST /api/v1/identify`

Searches the entire database to find the closest match for the provided face using high-speed FAISS search.

**Content-Type:** `multipart/form-data`

| Field | Type | Description |
| :--- | :--- | :--- |
| `images` | `files[]` | One or more images for matching and liveness check. |
| `audit_info` | `string` | (Optional) JSON for audit logs. |

---

### 4. System Administration

#### **System Settings**
`GET /admin/settings` | `PUT /admin/settings`

Manage the core behavior of the biometric engine.

| Setting | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `similarity_threshold` | `float` | `0.65` | Minimum score to consider a match a "Success". |
| `liveness_enabled` | `bool` | `true` | Toggle the anti-spoofing engine. |
| `max_attempts` | `int` | `3` | Recommended limit for verification retries. |

---

#### **Control Endpoints**

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/admin/reload-index` | Forces the FAISS engine to reload all templates from the DB into memory. |
| `GET` | `/health` | Returns system health, engine status, and current index size. |
| `GET` | `/` | API root and link to interactive Swagger docs. |

---

### Error Responses

The API uses standard HTTP status codes:
- `400 Bad Request`: Validation errors, no face detected, or student already registered.
- `404 Not Found`: Student ID does not exist.
- `500 Internal Server Error`: Unexpected AI engine or database failure.

Example Error:
```json
{
  "detail": "No face detected in image"
}
```

## Security Considerations
- Biometric data is never stored as raw images; only encrypted mathematical embeddings are persisted.
- Each verification attempt is logged with a similarity score and liveness result for audit trailing.
- Cosine similarity thresholding (default 0.65) ensures a balance between False Acceptance Rate (FAR) and False Rejection Rate (FRR).

## License
Confidential and Proprietary.
