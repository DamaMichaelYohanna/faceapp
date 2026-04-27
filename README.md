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

#### **Sync Student from External Source**
Students are no longer registered manually via this API. Instead, they are fetched from an external Main Storage API (or a built-in mock registry for development) during the enrollment process.

#### **List Enrolled Students**
`GET /api/v1/students/`

Retrieve a list of all students who have already completed biometric enrollment.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `skip` | `integer` | `0` | Number of records to skip. |
| `limit` | `integer` | `100` | Maximum number of records to return. |

---

### 2. Biometric Enrollment

#### **Enroll Face**
`POST /api/v1/enroll/upload`

Processes a face image, extracts a 512D embedding, and stores it. This endpoint automatically syncs the student's profile from the external storage if they are not already in the local database.

**Content-Type:** `multipart/form-data`

| Field | Type | Description |
| :--- | :--- | :--- |
| `matric_number` | `string` | The student's unique ID (Matric/Reg number). |
| `file` | `file` | A high-quality JPG or PNG image. |
| `metadata` | `string` | (Optional) JSON string for custom data. |

---

### 3. Verification & Identification

#### **Verify Student (1:1)**
`POST /api/v1/verify/{identifier}`

Compares live capture frames against the stored template. You can use either the internal Database ID or the external Matric Number as the `identifier`.

**Content-Type:** `multipart/form-data`

| Field | Type | Description |
| :--- | :--- | :--- |
| `identifier` | `string` | Path parameter. Internal ID or Matric Number. |
| `file` | `file` | Primary face image for verification. |
| `extra_frames` | `files[]` | Optional additional frames for liveness detection. |
| `audit_info` | `string` | (Optional) JSON string for audit trails. |

**Standard Response Schema:**
```json
{
  "matched": true,
  "student_id": 1,
  "confidence": 0.824,
  "mode": "1:1",
  "liveness_passed": true,
  "message": "Match successful"
}
```

---

#### **Identify Student (1:N)**
`POST /api/v1/identify`

Searches the entire database to find the closest match for the provided face.

**Content-Type:** `multipart/form-data`

| Field | Type | Description |
| :--- | :--- | :--- |
| `file` | `file` | Primary face image to identify. |
| `extra_frames` | `files[]` | Optional additional frames for liveness. |
| `audit_info` | `string` | (Optional) JSON for audit logs. |

---

### 4. System Administration & Security

#### **Admin Authentication**
`POST /api/v1/admin/login`

All administrative and core biometric endpoints (management, enrollment, verification, identification, and settings) are secured using OAuth2 with JWT Bearer tokens. You must obtain an access token to interact with these endpoints.

**Example Request:**
```bash
curl -X POST "http://localhost:8000/api/v1/admin/login" \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -d "username=admin&password=admin123"
```

**Response:**
```json
{
  "access_token": "eyJhbG...",
  "token_type": "bearer"
}
```
*Note: A default admin user is created on the first startup using the `ADMIN_USERNAME` and `ADMIN_PASSWORD` environment variables (defaults to `admin` / `admin123`). Pass the token in the `Authorization: Bearer <token>` header for secured endpoints.*

---

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

## Frontend Test Console
A sleek, single-page testing utility is provided in `frontend/index.html`. 
1. Open the file in any modern web browser.
2. Ensure the API server is running.
3. Use the console to log in, enroll students (using mock matric numbers), and perform biometric verification.

## License
Confidential and Proprietary.
