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

## Usage

### Running the Server
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### API Endpoints
- `POST /api/v1/students/`: Register a new student.
- `POST /api/v1/enroll/upload`: Upload an image to enroll a student in biometrics.
- `POST /api/v1/verify/{student_id}`: Perform 1:1 identity verification.
- `POST /api/v1/identify`: Perform 1:N identification across the entire database.
- `GET /admin/settings`: Retrieve current system configuration.
- `PUT /admin/settings`: Update threshold and security settings.

## Security Considerations
- Biometric data is never stored as raw images; only encrypted mathematical embeddings are persisted.
- Each verification attempt is logged with a similarity score and liveness result for audit trailing.
- Cosine similarity thresholding (default 0.65) ensures a balance between False Acceptance Rate (FAR) and False Rejection Rate (FRR).

## License
Confidential and Proprietary.
