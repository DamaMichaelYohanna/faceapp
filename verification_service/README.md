# Biometric Verification Service

Standalone service for 1:1 face verification and 1:N identification.

## Features
- **1:1 Verification**: Match a live face against a specific student's enrolled template.
- **1:N Identification**: Identify a student from a live face scan using FAISS for high-speed matching.
- **Domain Search**: Live lookup of student metadata from domain servers.
- **FAISS Integration**: High-performance in-memory index for biometric identification.
- **No RBAC**: Simplified logic for internal network deployment.

## Setup

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment**:
   Edit `.env` or set environment variables:
   - `DATABASE_URL`: Should point to the same database used by the Capturing Service for shared data access.
   - `BIOMETRIC_SECRET_KEY`: **Must match** the key used in the Capturing Service to decrypt stored templates.

3. **Initialize Configuration**:
   Call the configuration endpoint with the same master server credentials used in the Capturing Service to enable domain syncing:
   ```http
   POST /admin/config
   {
     "server_url": "https://master.example.com",
     "username": "operator_name",
     "public_key": "RSA_PUBLIC_KEY",
     "private_key": "RSA_PRIVATE_KEY",
     "aes_secret": "SHARED_AES_KEY"
   }
   ```

## Running the Service
```bash
python run_server.py
```
The service runs on port `8002` by default.

## API Documentation
Once running, visit `http://localhost:8002/docs` for the interactive Swagger UI.
