"""Check DB state for debugging the 500 error."""
import sys
sys.path.insert(0, '.')
from db import database, models

db = database.SessionLocal()

# Check settings
s = db.query(models.SystemSettings).first()
if s:
    print(f"Settings found: liveness_enabled={s.liveness_enabled}, threshold={s.similarity_threshold}")
else:
    print("!!! NO SETTINGS ROW - this would cause NoneType 500 !!!")

# Check users
users = db.query(models.User).all()
print(f"\nUsers ({len(users)}):")
for u in users:
    print(f"  {u.username} role={u.role} active={u.is_active}")

# Check students
students = db.query(models.Student).all()
print(f"\nStudents ({len(students)}):")
for st in students:
    print(f"  id={st.id} external_id={st.external_id} name={st.full_name} enrolled={st.biometric_enrolled}")

# Check enrollments
enrollments = db.query(models.FaceEnrollment).all()
print(f"\nEnrollments ({len(enrollments)}):")
for e in enrollments:
    print(f"  id={e.id} student_id={e.student_id} status={e.status}")

db.close()
