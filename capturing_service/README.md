# Biometric Capturing Service

Standalone service for face biometric enrollment with offline-first capabilities.

## Features
- **Offline-first Enrollment**: Capture and save face templates locally even without an internet connection.
- **Upstream Sync**: Batch upload pending enrollments to a master server using AES-256-CBC encryption.
- **Domain Synchronization**: Cache department and student metadata from multiple domain servers.
- **Liveness Detection**: Multi-frame passive liveness check.
- **No RBAC**: Simplified logic for internal network deployment.

## Setup

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment**:
   Edit `.env` or set environment variables:
   - `DATABASE_URL`: Path to the SQLite DB or Postgres URI.
   - `BIOMETRIC_SECRET_KEY`: Secret used for local template encryption.
   - `AES_SECRET`: Symmetric key used for upstream upload encryption (must match master server).

3. **Initialize Configuration**:
   After starting the server, call the configuration endpoint with your master server credentials:
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

4. **Sync Domains**:
   Pull metadata from the master server:
   ```http
   POST /api/v1/domains/sync
   ```

## Running the Service
```bash
python run_server.py
```
The service runs on port `8001` by default.

## API Documentation
Once running, visit `http://localhost:8001/docs` for the interactive Swagger UI.
