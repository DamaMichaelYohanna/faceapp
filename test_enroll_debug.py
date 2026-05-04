"""Quick script to reproduce the enroll 500 error and show the server traceback."""
import requests
import numpy as np
import cv2
import io

BASE = "http://127.0.0.1:8000"

# 1. Login
login = requests.post(f"{BASE}/api/v1/auth/login", data={"username": "admin", "password": "admin123"})
print(f"Login: {login.status_code}")
token = login.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# 2. Create a synthetic test image (a valid JPEG with a face-like shape)
img = np.zeros((400, 400, 3), dtype=np.uint8)
img[:] = (200, 180, 160)  # skin-tone background
# Draw a simple face
cv2.circle(img, (200, 200), 120, (220, 200, 180), -1)  # face
cv2.circle(img, (170, 170), 15, (50, 50, 50), -1)  # left eye
cv2.circle(img, (230, 170), 15, (50, 50, 50), -1)  # right eye
cv2.ellipse(img, (200, 240), (30, 15), 0, 0, 180, (50, 50, 50), 2)  # mouth

_, buf = cv2.imencode('.jpg', img)
img_bytes = buf.tobytes()
print(f"Test image size: {len(img_bytes)} bytes")

# 3. Send enrollment request (single file, like the frontend would with 1 capture frame)
files = [("files", ("capture.jpg", io.BytesIO(img_bytes), "image/jpeg"))]
data = {"external_id": "FT22ACMP0833", "matric_number": "FT22ACMP0833"}

print(f"\nSending enrollment request...")
resp = requests.post(f"{BASE}/api/v1/enroll", headers=headers, files=files, data=data, timeout=30)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text}")

# 4. Try with multiple files (simulating liveness frames)
if resp.status_code != 201:
    print(f"\n--- Trying with multiple frames ---")
    frames = []
    for i in range(5):
        noise = np.random.randint(-5, 5, img.shape, dtype=np.int16)
        frame = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        _, fbuf = cv2.imencode('.jpg', frame)
        frames.append(("files", (f"frame_{i}.jpg", io.BytesIO(fbuf.tobytes()), "image/jpeg")))
    
    resp2 = requests.post(f"{BASE}/api/v1/enroll", headers=headers, files=frames, data=data, timeout=30)
    print(f"Status: {resp2.status_code}")
    print(f"Response: {resp2.text}")
