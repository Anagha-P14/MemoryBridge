"""Create a local MemoryBridge account without public self-registration.
Example: python scripts/create_user.py --email doctor@demo.local --name "Demo Doctor" --role doctor --patient MB-001
"""
import argparse, getpass, uuid
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import db, init_db, pw_hash

p=argparse.ArgumentParser()
p.add_argument('--email',required=True); p.add_argument('--name',required=True); p.add_argument('--role',required=True,choices=['caregiver','helper','doctor']); p.add_argument('--patient',default='MB-001')
a=p.parse_args(); password=getpass.getpass('Password (minimum 12 characters): ')
if len(password)<12: raise SystemExit('Password must be at least 12 characters.')
init_db(); uid=uuid.uuid4().hex
with db() as c:
    if not c.execute('SELECT 1 FROM patients WHERE id=?',(a.patient,)).fetchone():
        c.execute('INSERT INTO patients(id,display_name,created_at) VALUES(?,?,?)',(a.patient,'Patient '+a.patient,datetime.now(timezone.utc).isoformat()))
    c.execute('INSERT INTO users(id,email,display_name,role,password_hash,active,created_at) VALUES(?,?,?,?,?,1,?)',(uid,a.email.lower(),a.name,a.role,pw_hash(password),datetime.now(timezone.utc).isoformat()))
    c.execute('INSERT INTO relationships(patient_id,user_id,permission) VALUES(?,?,?)',(a.patient,uid,'write'))
print(f'Created {a.role} account {a.email} linked to patient {a.patient}.')
