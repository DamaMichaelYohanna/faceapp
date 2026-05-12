# Biometric Enrollment & Verification System

A distributed biometric solution split into two specialized services for high-performance face enrollment and identity verification.

## 🏗️ System Architecture

This repository contains two independent services designed to work with a **Two-Tier Upstream API Model** (Master and Domain servers).

### 1. [Capturing Service](./capturing_service) (Port 8001)
Dedicated to face enrollment with an **offline-first** strategy.
- **Local Enrollment**: Capture face templates even without an internet connection.
- **Upstream Sync**: Securely batch-upload templates to a Master Server using **AES-256-CBC** encryption.
- **Metadata Cache**: Locally caches departments and student lists from Domain Servers.

### 2. [Verification Service](./verification_service) (Port 8002)
Dedicated to identity confirmation and searching.
- **1:1 Verification**: Match a live face against a specific enrolled student.
- **1:N Identification**: High-speed identity search using **FAISS** in-memory indexing.
- **Live Lookup**: Real-time student data search against Domain Servers.

---

## 🛠️ Technology Stack
- **AI Engine**: InsightFace (ArcFace `buffalo_l` model)
- **Search Engine**: FAISS (Facebook AI Similarity Search)
- **Framework**: FastAPI (Python)
- **Database**: SQLite / PostgreSQL (SQLAlchemy)
- **Encryption**: AES-256 (Upstream), Fernet (Local storage)

---

## 🚀 Quick Start

### 1. Installation
Each service has its own `requirements.txt`. It is recommended to use virtual environments.

```bash
# Install for Capturing Service
cd capturing_service
pip install -r requirements.txt

# Install for Verification Service
cd ../verification_service
pip install -r requirements.txt
```

### 2. Configuration
Each service contains a `.env` file for configuration. 
- **Important**: For local testing, ensure both services share the same `DATABASE_URL` and `BIOMETRIC_SECRET_KEY` so they can read each other's data.

### 3. Running
```bash
# Terminal 1
cd capturing_service && python run_server.py

# Terminal 2
cd verification_service && python run_server.py
```

---

## 📖 Documentation
Detailed API documentation and setup guides are available in the respective service directories:
- [Capturing Service Documentation](./capturing_service/README.md)
- [Verification Service Documentation](./verification_service/README.md)
- [Upstream API Specification](./domain-data-api-spec.md)

---

## 🛡️ Security & Privacy
- **No Raw Images**: The system only stores mathematical 512-D face embeddings.
- **Encryption at Rest**: Templates are encrypted locally before being saved to the database.
- **Secure Sync**: Data uploaded to the Master Server is AES-encrypted with a shared symmetric key.
- **Liveness Detection**: Multi-frame passive liveness checks prevent spoofing via photos or screens.
