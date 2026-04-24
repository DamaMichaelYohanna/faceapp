import httpx
import json
import time
import os
from PIL import Image
import io

BASE_URL = "http://127.0.0.1:8000"

def create_test_image(color=(255, 0, 0), size=(300, 300)):
    file = io.BytesIO()
    image = Image.new('RGB', size, color)
    image.save(file, 'jpeg')
    file.seek(0)
    return file

def test_flow():
    # 1. Create a Student
    print("--- Creating Student ---")
    with httpx.Client() as client:
        response = client.post(
            f"{BASE_URL}/api/v1/students/",
            json={"external_id": "STU12345", "full_name": "John Doe"}
        )
        if response.status_code == 400 and "already registered" in response.text:
            print("Student already exists, fetching detail...")
            # Get list and find id
            students = client.get(f"{BASE_URL}/api/v1/students/").json()
            student = next(s for s in students if s["external_id"] == "STU12345")
        else:
            student = response.json()
        
        student_id = student["id"]
        print(f"Student ID: {student_id}")

        # 2. Check Enrollment Status (Should be false)
        print("\n--- Checking Enrollment Status ---")
        status = client.get(f"{BASE_URL}/api/v1/enroll/status/{student_id}").json()
        print(f"Is Enrolled: {status['is_enrolled']}")

        # 3. Enroll Face
        print("\n--- Enrolling Face ---")
        img1 = create_test_image(color=(100, 200, 100))
        files = {"file": ("enroll.jpg", img1, "image/jpeg")}
        data = {"student_id": student_id, "metadata": json.dumps({"device": "Scanner-01"})}
        
        response = client.post(f"{BASE_URL}/api/v1/enroll/upload", data=data, files=files)
        print(f"Enrollment Response: {response.json()}")

        # 4. Verify Face (Same Image - Should Match)
        print("\n--- Verifying Face (Matching Image) ---")
        img1.seek(0)
        files = {"file": ("verify1.jpg", img1, "image/jpeg")}
        data = {"student_id": student_id, "audit_info": json.dumps({"location": "Main Entrance"})}
        
        response = client.post(f"{BASE_URL}/api/v1/verify", data=data, files=files)
        verification = response.json()
        print(f"Is Match: {verification['is_successful']} (Score: {verification['match_score']:.4f})")

        # 5. Verify Face (Different Image - Should Fail Match)
        print("\n--- Verifying Face (Different Image) ---")
        img2 = create_test_image(color=(50, 50, 50))
        files = {"file": ("verify2.jpg", img2, "image/jpeg")}
        
        response = client.post(f"{BASE_URL}/api/v1/verify", data=data, files=files)
        verification = response.json()
        print(f"Is Match: {verification['is_successful']} (Score: {verification['match_score']:.4f})")

        # 6. Get Verification Logs
        print("\n--- Fetching Verification Logs ---")
        logs = client.get(f"{BASE_URL}/api/v1/verify/logs/{student_id}").json()
        for log in logs:
            print(f"Time: {log['timestamp']} | Match: {log['is_successful']} | Score: {log['match_score']:.4f}")

if __name__ == "__main__":
    # Ensure server is running or prompt user to start it
    print("Ensure you have started the server using: python main.py")
    try:
        test_flow()
    except Exception as e:
        print(f"Error: {e}")
        print("Maybe the server is not running?")
