from fastapi.testclient import TestClient
from app.main import app

def test_health_and_role_access():
    with TestClient(app) as c:
        assert c.get('/health').status_code == 200
        r=c.post('/api/auth/login',json={'email':'caregiver@example.test','password':'CareDemo123!'})
        assert r.status_code == 200 and r.json()['role']=='caregiver'
        assert c.get('/api/doctor/review-queue').status_code == 403
        assert c.get('/api/patients').status_code == 200

def test_doctor_review_queue():
    with TestClient(app) as c:
        r=c.post('/api/auth/login',json={'email':'doctor@example.test','password':'DoctorDemo123!'})
        assert r.status_code == 200 and r.json()['role']=='doctor'
        assert c.get('/api/doctor/review-queue').status_code == 200
