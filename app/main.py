"""MemoryBridge portfolio MVP: role-based care coordination with synthetic demo data.

Not for real patient care. SQLite is used for easy local setup; migrate to PostgreSQL
and complete security/clinical review before any production use.
"""
from __future__ import annotations
import hashlib, hmac, json, os, secrets, smtplib, sqlite3, time, uuid
from email.message import EmailMessage
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .pipeline import run_case
from . import llm

ROOT = Path(__file__).parent
PROJECT = ROOT.parent
DATA = Path(os.getenv("MB_DATA_DIR", PROJECT / "data"))
DB_PATH = Path(os.getenv("MB_DB_PATH", DATA / "memorybridge.db"))
UPLOAD_DIR = Path(os.getenv("MB_UPLOAD_DIR", DATA / "private_uploads"))
AUDIT = Path(os.getenv("AUDIT_LOG", DATA / "audit.jsonl"))
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-only-change-this-session-secret")
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}

app = FastAPI(title="MemoryBridge Care Coordination", version="0.2.0")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax", https_only=os.getenv("COOKIE_HTTPS_ONLY", "false").lower() == "true", max_age=60*60*8)


def db():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def pw_hash(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return salt.hex() + "$" + digest.hex()


def pw_verify(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 310_000).hex()
        return hmac.compare_digest(candidate, digest_hex)
    except Exception:
        return False


def audit(action: str, actor: str, resource: str = "", detail: str = ""):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    # Do not log passwords, notes, document contents, tokens, or clinical details.
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "action": action,
                            "actor_id": actor, "resource_id": resource, "detail": detail}, ensure_ascii=False) + "\n")


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('caregiver','helper','doctor')), password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS patients(id TEXT PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS relationships(patient_id TEXT NOT NULL REFERENCES patients(id), user_id TEXT NOT NULL REFERENCES users(id), permission TEXT NOT NULL DEFAULT 'write', PRIMARY KEY(patient_id,user_id));
        CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id), author_id TEXT NOT NULL REFERENCES users(id), note TEXT NOT NULL, created_at TEXT NOT NULL, case_json TEXT, review_status TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id), uploader_id TEXT NOT NULL REFERENCES users(id), original_name TEXT NOT NULL, stored_name TEXT NOT NULL, content_type TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS appointments(id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id), doctor_id TEXT NOT NULL REFERENCES users(id), requested_by TEXT NOT NULL REFERENCES users(id), start_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'requested', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS medication_records(id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id), doctor_id TEXT NOT NULL REFERENCES users(id), instructions TEXT NOT NULL, effective_date TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS followups(id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(id), doctor_id TEXT NOT NULL REFERENCES users(id), due_at TEXT NOT NULL, instructions TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, observation_id TEXT NOT NULL REFERENCES observations(id), reviewer_id TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, recipient_id TEXT NOT NULL REFERENCES users(id), event_type TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
        """)
        # Synthetic demo identities only. Change/remove before deployment.
        if DEMO_MODE:
            demo_users = [
                ("demo-caregiver", "caregiver@example.test", "Demo Caregiver", "caregiver", "CareDemo123!"),
                ("demo-helper", "helper@example.test", "Demo Helper", "helper", "HelperDemo123!"),
                ("demo-doctor", "doctor@example.test", "Demo Doctor", "doctor", "DoctorDemo123!"),
            ]
            for uid, email, name, role, pw in demo_users:
                c.execute("INSERT OR IGNORE INTO users(id,email,display_name,role,password_hash,active,created_at) VALUES(?,?,?,?,?,1,?)",
                          (uid,email,name,role,pw_hash(pw),datetime.now(timezone.utc).isoformat()))
            c.execute("INSERT OR IGNORE INTO patients(id,display_name,created_at) VALUES(?,?,?)",
                      ("MB-001", "Demo Patient MB-001", datetime.now(timezone.utc).isoformat()))
            for uid in ("demo-caregiver", "demo-helper", "demo-doctor"):
                c.execute("INSERT OR IGNORE INTO relationships(patient_id,user_id,permission) VALUES('MB-001',?,'write')", (uid,))


@app.on_event("startup")
def startup():
    if not DEMO_MODE and SESSION_SECRET == "dev-only-change-this-session-secret":
        raise RuntimeError("Set a strong SESSION_SECRET before starting with DEMO_MODE=false")
    init_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(401, "Please log in")
    with db() as c:
        row = c.execute("SELECT id,email,display_name,role,active FROM users WHERE id=?", (uid,)).fetchone()
    if not row or not row["active"]:
        request.session.clear()
        raise HTTPException(401, "Session expired or account disabled")
    return dict(row)


def require_role(user, *roles):
    if user["role"] not in roles:
        raise HTTPException(403, "Your account is not permitted to perform this action")


def patient_access(user, patient_id: str, write: bool = False):
    with db() as c:
        row = c.execute("SELECT permission FROM relationships WHERE patient_id=? AND user_id=?", (patient_id, user["id"])).fetchone()
    if not row or (write and row["permission"] not in ("write", "owner")):
        raise HTTPException(404, "Patient not found or access not granted")


def iso_now(): return datetime.now(timezone.utc).isoformat()

def send_email(to_email: str, subject: str, body: str, recipient_id: str, event_type: str):
    """Optional SMTP notification. Never puts clinical details in the email body."""
    host=os.getenv("SMTP_HOST", ""); port=int(os.getenv("SMTP_PORT", "587")); sender=os.getenv("SMTP_FROM", "")
    status="skipped_no_smtp_config"
    if host and sender and to_email and not to_email.endswith(".test"):
        msg=EmailMessage(); msg["Subject"]=subject; msg["From"]=sender; msg["To"]=to_email; msg.set_content(body)
        try:
            with smtplib.SMTP(host,port,timeout=15) as server:
                if os.getenv("SMTP_STARTTLS", "true").lower()=="true": server.starttls()
                if os.getenv("SMTP_USER"): server.login(os.getenv("SMTP_USER"),os.getenv("SMTP_PASSWORD", ""))
                server.send_message(msg)
            status="sent"
        except Exception as exc:
            status="failed_"+type(exc).__name__
    with db() as c:
        c.execute("INSERT INTO notifications(id,recipient_id,event_type,status,created_at,detail) VALUES(?,?,?,?,?,?)",(uuid.uuid4().hex,recipient_id,event_type,status,iso_now(),"Email status only; no clinical content stored"))
    audit("notification_"+status, "system", recipient_id, event_type)
    return status



class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)

class ObservationIn(BaseModel):
    patient_id: str = "MB-001"
    note: str = Field(min_length=12, max_length=12000)
    prior_letter: str = Field(default="", max_length=12000)
    target_lang: str = "en"

class AppointmentIn(BaseModel):
    patient_id: str = "MB-001"
    doctor_id: str = "demo-doctor"
    start_at: str
    reason: str = Field(default="", max_length=1000)

class ReviewIn(BaseModel):
    action: str
    note: str = Field(default="", max_length=3000)

class MedicationIn(BaseModel):
    patient_id: str = "MB-001"
    instructions: str = Field(min_length=3, max_length=5000)
    effective_date: str

class FollowupIn(BaseModel):
    patient_id: str = "MB-001"
    due_at: str
    instructions: str = Field(min_length=3, max_length=3000)


@app.get("/health")
def health():
    return {"ok": True, "service": "MemoryBridge", "mode": "demo" if DEMO_MODE else "configured"}

@app.get("/api/public-stats")
def public_stats():
    # Only expose aggregate synthetic counts in local demo mode.
    if not DEMO_MODE:
        return {"available": False}
    with db() as c:
        patients_count=c.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        pending=c.execute("SELECT COUNT(*) FROM observations WHERE review_status='pending'").fetchone()[0]
        appts=c.execute("SELECT COUNT(*) FROM appointments WHERE status IN ('requested','confirmed')").fetchone()[0]
    return {"available": True,"patients":patients_count,"pending_reviews":pending,"appointments":appts,"synthetic_demo_data":True}

@app.get("/api/model-status")
def model_status():
    return {"mode": "live-configured" if llm.BASE else "mock", "model": llm.MODEL if llm.BASE else None,
            "note": "Configured endpoint is not proof of GPU execution; verify provider/model telemetry. Model failures are shown as manual-review-required."}

@app.post("/api/auth/login")
def login(body: LoginIn, request: Request):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (body.email.strip(),)).fetchone()
    if not row or not row["active"] or not pw_verify(body.password, row["password_hash"]):
        audit("login_failed", "anonymous")
        raise HTTPException(401, "Email or password is incorrect")
    request.session.clear()
    request.session["uid"] = row["id"]
    audit("login_success", row["id"])
    return {"id": row["id"], "email": row["email"], "display_name": row["display_name"], "role": row["role"]}

@app.post("/api/auth/logout")
def logout(request: Request):
    uid = request.session.get("uid", "anonymous")
    request.session.clear(); audit("logout", uid)
    return {"ok": True}

@app.get("/api/me")
def me(request: Request): return current_user(request)

@app.get("/api/patients")
def patients(request: Request):
    u = current_user(request)
    with db() as c:
        rows = c.execute("SELECT p.id,p.display_name FROM patients p JOIN relationships r ON r.patient_id=p.id WHERE r.user_id=? ORDER BY p.display_name", (u["id"],)).fetchall()
    return [dict(x) for x in rows]

@app.post("/api/observations")
def create_observation(body: ObservationIn, request: Request):
    u = current_user(request); require_role(u, "caregiver", "helper")
    patient_access(u, body.patient_id, write=True)
    # Rule-based triage still runs if AI is unavailable. A failed model is not silently presented as a successful summary.
    result = run_case(body.note, body.prior_letter, body.target_lang)
    llm_failed = any("LLM failed" in str(x) for x in result.get("reasons", [])) or "mock-fallback" in result.get("meta", {}).get("llm_mode", "")
    if llm_failed:
        result["status"] = "AI_UNAVAILABLE_MANUAL_REVIEW_REQUIRED"
        result.setdefault("reasons", []).append("AI service unavailable or returned invalid output. Original note retained; clinician must review manually.")
        result["findings"] = []
        result["patient_view"]["body"] = "AI summary unavailable. Your original observation has been saved for clinician review."
    oid = uuid.uuid4().hex
    with db() as c:
        c.execute("INSERT INTO observations(id,patient_id,author_id,note,created_at,case_json,review_status) VALUES(?,?,?,?,?,?,?)",
                  (oid, body.patient_id, u["id"], body.note, iso_now(), json.dumps(result, ensure_ascii=False), "pending"))
    audit("observation_created", u["id"], oid, result.get("status", ""))
    # Return summary to submitter, but never include server-side redaction mapping.
    result.pop("redaction", None); result.pop("_mapping", None)
    return {"id": oid, "patient_id": body.patient_id, "review_status": "pending", **result}

@app.get("/api/doctor/review-queue")
def review_queue(request: Request):
    u = current_user(request); require_role(u, "doctor")
    with db() as c:
        rows = c.execute("SELECT o.id,o.patient_id,o.created_at,o.review_status,p.display_name,o.case_json FROM observations o JOIN patients p ON p.id=o.patient_id JOIN relationships r ON r.patient_id=o.patient_id WHERE r.user_id=? ORDER BY o.created_at DESC", (u["id"],)).fetchall()
    out=[]
    for r in rows:
        payload=json.loads(r["case_json"] or "{}")
        out.append({"id":r["id"],"patient_id":r["patient_id"],"patient":r["display_name"],"created_at":r["created_at"],"review_status":r["review_status"],"status":payload.get("status","NEEDS_CLINICIAN_REVIEW"),"flags":[x.get("label") for x in payload.get("red_flags",[]) ]})
    return out

@app.get("/api/observations/{oid}")
def get_observation(oid: str, request: Request):
    u=current_user(request)
    with db() as c: row=c.execute("SELECT * FROM observations WHERE id=?",(oid,)).fetchone()
    if not row: raise HTTPException(404,"Observation not found")
    if u["role"] == "doctor": patient_access(u,row["patient_id"])
    elif u["role"] in ("caregiver","helper"):
        patient_access(u,row["patient_id"])
        if row["author_id"] != u["id"]: # linked carers can see the case, not the original author's private draft internals
            pass
    payload=json.loads(row["case_json"] or "{}")
    payload.pop("_mapping",None)
    payload["original_note"] = row["note"] if u["role"] == "doctor" or row["author_id"] == u["id"] else None
    payload.update({"id":row["id"],"patient_id":row["patient_id"],"review_status":row["review_status"]})
    audit("observation_viewed",u["id"],oid)
    return payload

@app.post("/api/observations/{oid}/review")
def review_observation(oid: str, body: ReviewIn, request: Request):
    u=current_user(request); require_role(u,"doctor")
    if body.action not in ("approve","needs_info","reject"): raise HTTPException(400,"Invalid review action")
    with db() as c:
        row=c.execute("SELECT patient_id FROM observations WHERE id=?",(oid,)).fetchone()
        if not row: raise HTTPException(404,"Observation not found")
        patient_access(u,row["patient_id"])
        c.execute("INSERT INTO reviews(id,observation_id,reviewer_id,action,note,created_at) VALUES(?,?,?,?,?,?)",(uuid.uuid4().hex,oid,u["id"],body.action,body.note,iso_now()))
        c.execute("UPDATE observations SET review_status=? WHERE id=?",(body.action,oid))
    audit("observation_reviewed",u["id"],oid,body.action)
    notify_status="not_applicable"
    if body.action == "approve":
        with db() as c:
            authors=c.execute("SELECT DISTINCT u.id,u.email FROM users u JOIN observations o ON o.author_id=u.id WHERE o.id=? AND u.role IN ('caregiver','helper')",(oid,)).fetchall()
        notify_statuses=[send_email(a["email"],"MemoryBridge visit summary reviewed","A clinician has reviewed your visit-preparation summary. Sign in to MemoryBridge to view the update.",a["id"],"review.approved") for a in authors]
        notify_status=",".join(notify_statuses) if notify_statuses else "no_caregiver_recipient"
    return {"ok":True,"review_status":body.action,"notification_status":notify_status,"message":"Review saved. Only clinician-authored instructions should be sent as clinical guidance."}

@app.post("/api/appointments")
def request_appointment(body: AppointmentIn, request: Request):
    u=current_user(request); require_role(u,"caregiver","helper")
    patient_access(u,body.patient_id,write=True)
    try:
        dt=datetime.fromisoformat(body.start_at.replace("Z","+00:00"))
    except ValueError: raise HTTPException(400,"start_at must be an ISO date/time")
    if dt.tzinfo is None: raise HTTPException(400,"Include a timezone offset in start_at")
    with db() as c:
        doc=c.execute("SELECT id,role FROM users WHERE id=? AND active=1",(body.doctor_id,)).fetchone()
        if not doc or doc["role"]!="doctor": raise HTTPException(400,"Selected doctor is not available")
        if not c.execute("SELECT 1 FROM relationships WHERE patient_id=? AND user_id=?",(body.patient_id,body.doctor_id)).fetchone(): raise HTTPException(403,"Doctor is not assigned to this patient")
        appt_id=uuid.uuid4().hex
        c.execute("INSERT INTO appointments(id,patient_id,doctor_id,requested_by,start_at,reason,status,created_at,updated_at) VALUES(?,?,?,?,?,?, 'requested',?,?)",(appt_id,body.patient_id,body.doctor_id,u["id"],dt.isoformat(),body.reason,iso_now(),iso_now()))
    audit("appointment_requested",u["id"],appt_id)
    return {"id":appt_id,"status":"requested","start_at":dt.isoformat(),"message":"Appointment request submitted; it is not confirmed until the doctor accepts it."}

@app.get("/api/appointments")
def list_appointments(request: Request):
    u=current_user(request)
    with db() as c:
        if u["role"]=="doctor": rows=c.execute("SELECT a.*,p.display_name AS patient_name FROM appointments a JOIN patients p ON p.id=a.patient_id WHERE a.doctor_id=? ORDER BY a.start_at",(u["id"],)).fetchall()
        else: rows=c.execute("SELECT a.*,p.display_name AS patient_name FROM appointments a JOIN patients p ON p.id=a.patient_id WHERE a.requested_by=? ORDER BY a.start_at",(u["id"],)).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/appointments/{aid}/status")
def appointment_status(aid: str, status: str, request: Request):
    u=current_user(request); require_role(u,"doctor")
    if status not in ("confirmed","rejected","cancelled"): raise HTTPException(400,"Invalid status")
    with db() as c:
        row=c.execute("SELECT * FROM appointments WHERE id=?",(aid,)).fetchone()
        if not row: raise HTTPException(404,"Appointment not found")
        if row["doctor_id"]!=u["id"]: raise HTTPException(403,"Not your appointment")
        c.execute("UPDATE appointments SET status=?,updated_at=? WHERE id=?",(status,iso_now(),aid))
    audit("appointment_status_changed",u["id"],aid,status)
    notify_status="not_applicable"
    if status in ("confirmed","rejected"):
        with db() as c:
            recipient=c.execute("SELECT email,id FROM users WHERE id=?",(row["requested_by"],)).fetchone()
        if recipient:
            subject="MemoryBridge appointment update"
            message="Your appointment request has been updated. Sign in to MemoryBridge to view the confirmed details."
            if status=="rejected": message="Your appointment request was not accepted. Please sign in to MemoryBridge to request another time."
            notify_status=send_email(recipient["email"],subject,message,recipient["id"],"appointment."+status)
    return {"ok":True,"status":status,"notification_status":notify_status,"calendar_url":f"/api/appointments/{aid}/calendar.ics" if status=="confirmed" else None}

@app.get("/api/appointments/{aid}/calendar.ics")
def calendar_invite(aid: str, request: Request):
    u=current_user(request)
    with db() as c: row=c.execute("SELECT a.*,p.display_name FROM appointments a JOIN patients p ON p.id=a.patient_id WHERE a.id=?",(aid,)).fetchone()
    if not row: raise HTTPException(404,"Appointment not found")
    if u["id"] not in (row["requested_by"],row["doctor_id"]): patient_access(u,row["patient_id"])
    if row["status"]!="confirmed": raise HTTPException(409,"Calendar invitation is available only after confirmation")
    dt=datetime.fromisoformat(row["start_at"].replace("Z","+00:00"))
    start=dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end=datetime.fromtimestamp(dt.timestamp()+30*60,timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_title="MemoryBridge care appointment"
    ics=f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//MemoryBridge//Care Coordination//EN\r\nCALSCALE:GREGORIAN\r\nBEGIN:VEVENT\r\nUID:{aid}@memorybridge.local\r\nDTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}\r\nDTSTART:{start}\r\nDTEND:{end}\r\nSUMMARY:{safe_title}\r\nDESCRIPTION:Please sign in to MemoryBridge for details.\r\nSTATUS:CONFIRMED\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    return Response(ics,media_type="text/calendar",headers={"Content-Disposition": f'attachment; filename="memorybridge-{aid[:8]}.ics"'})

@app.post("/api/patients/{patient_id}/documents")
async def upload_document(patient_id: str, request: Request, file: UploadFile = File(...)):
    u=current_user(request); require_role(u,"caregiver","helper","doctor")
    patient_access(u,patient_id,write=True)
    name=Path(file.filename or "upload").name
    ext=Path(name).suffix.lower()
    if ext not in ALLOWED_EXTS: raise HTTPException(415,"Allowed formats: PDF, PNG, JPG, WEBP")
    content=await file.read(MAX_UPLOAD+1)
    if len(content)>MAX_UPLOAD: raise HTTPException(413,"File exceeds configured upload limit")
    # Basic content signature checks; production requires malware scanning and stronger file validation.
    valid=(ext==".pdf" and content.startswith(b"%PDF")) or (ext==".png" and content.startswith(b"\x89PNG\r\n\x1a\n")) or (ext in (".jpg",".jpeg") and content.startswith(b"\xff\xd8\xff")) or (ext==".webp" and content[:4]==b"RIFF" and content[8:12]==b"WEBP")
    if not valid: raise HTTPException(415,"File content does not match its extension")
    did=uuid.uuid4().hex; stored=f"{did}{ext}"; UPLOAD_DIR.mkdir(parents=True,exist_ok=True); (UPLOAD_DIR/stored).write_bytes(content)
    with db() as c: c.execute("INSERT INTO documents(id,patient_id,uploader_id,original_name,stored_name,content_type,created_at) VALUES(?,?,?,?,?,?,?)",(did,patient_id,u["id"],name,stored,file.content_type,iso_now()))
    audit("document_uploaded",u["id"],did,ext)
    return {"id":did,"patient_id":patient_id,"filename":name,"message":"Uploaded to private local storage. Configure malware scanning before production use."}

@app.get("/api/patients/{patient_id}/documents")
def list_documents(patient_id: str, request: Request):
    u=current_user(request); patient_access(u,patient_id)
    with db() as c: rows=c.execute("SELECT id,original_name,content_type,created_at,uploader_id FROM documents WHERE patient_id=? ORDER BY created_at DESC",(patient_id,)).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/documents/{did}/download")
def download_document(did: str, request: Request):
    u=current_user(request)
    with db() as c: row=c.execute("SELECT * FROM documents WHERE id=?",(did,)).fetchone()
    if not row: raise HTTPException(404,"Document not found")
    patient_access(u,row["patient_id"])
    path=UPLOAD_DIR/row["stored_name"]
    if not path.exists(): raise HTTPException(404,"Stored document missing")
    audit("document_downloaded",u["id"],did)
    return FileResponse(path,filename=row["original_name"],media_type=row["content_type"] or "application/octet-stream")

@app.post("/api/patients/{patient_id}/medication-records")
def add_medication(patient_id: str, body: MedicationIn, request: Request):
    u=current_user(request); require_role(u,"doctor"); patient_access(u,patient_id,write=True)
    if body.patient_id != patient_id: raise HTTPException(400,"Patient ID mismatch")
    mid=uuid.uuid4().hex
    with db() as c: c.execute("INSERT INTO medication_records(id,patient_id,doctor_id,instructions,effective_date,created_at) VALUES(?,?,?,?,?,?)",(mid,patient_id,u["id"],body.instructions,body.effective_date,iso_now()))
    audit("medication_record_created",u["id"],mid,"clinician-authored")
    with db() as c:
        recipients=c.execute("SELECT u.id,u.email FROM users u JOIN relationships r ON r.user_id=u.id WHERE r.patient_id=? AND u.role IN ('caregiver','helper')",(patient_id,)).fetchall()
    statuses=[send_email(r["email"],"MemoryBridge clinician update","A clinician has updated a patient record. Sign in to MemoryBridge and review the authorized details.",r["id"],"medication_record.created") for r in recipients]
    return {"id":mid,"status":"saved","notification_statuses":statuses,"message":"Record saved by authenticated doctor. Confirm details with the patient/caregiver through the approved workflow."}

@app.post("/api/patients/{patient_id}/follow-ups")
def add_followup(patient_id: str, body: FollowupIn, request: Request):
    u=current_user(request); require_role(u,"doctor"); patient_access(u,patient_id,write=True)
    fid=uuid.uuid4().hex
    with db() as c: c.execute("INSERT INTO followups(id,patient_id,doctor_id,due_at,instructions,created_at) VALUES(?,?,?,?,?,?)",(fid,patient_id,u["id"],body.due_at,body.instructions,iso_now()))
    audit("followup_created",u["id"],fid)
    with db() as c:
        recipients=c.execute("SELECT u.id,u.email FROM users u JOIN relationships r ON r.user_id=u.id WHERE r.patient_id=? AND u.role IN ('caregiver','helper')",(patient_id,)).fetchall()
    statuses=[send_email(r["email"],"MemoryBridge follow-up update","A clinician has added a follow-up. Sign in to MemoryBridge to review the details.",r["id"],"followup.created") for r in recipients]
    return {"id":fid,"status":"saved","notification_statuses":statuses}

@app.get("/api/dashboard/stats")
def dashboard_stats(request: Request):
    u=current_user(request)
    with db() as c:
        if u["role"]=="doctor":
            patients_count=c.execute("SELECT COUNT(DISTINCT patient_id) FROM relationships WHERE user_id=?",(u["id"],)).fetchone()[0]
            pending=c.execute("SELECT COUNT(*) FROM observations o JOIN relationships r ON r.patient_id=o.patient_id WHERE r.user_id=? AND o.review_status='pending'",(u["id"],)).fetchone()[0]
            appts=c.execute("SELECT COUNT(*) FROM appointments WHERE doctor_id=? AND status IN ('requested','confirmed')",(u["id"],)).fetchone()[0]
        else:
            patients_count=c.execute("SELECT COUNT(*) FROM relationships WHERE user_id=?",(u["id"],)).fetchone()[0]
            pending=c.execute("SELECT COUNT(*) FROM observations WHERE author_id=? AND review_status='pending'",(u["id"],)).fetchone()[0]
            appts=c.execute("SELECT COUNT(*) FROM appointments WHERE requested_by=? AND status IN ('requested','confirmed')",(u["id"],)).fetchone()[0]
    return {"patients":patients_count,"pending_reviews":pending,"upcoming_or_requested_appointments":appts,"data_source":"database"}

app.mount("/static", StaticFiles(directory=ROOT/"static"), name="static")
@app.get("/")
def index(): return FileResponse(ROOT/"static"/"index.html")
