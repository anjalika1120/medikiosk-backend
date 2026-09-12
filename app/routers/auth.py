import json
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import DBUser, DBIntakeSession
from app.schemas import RegisterRequest, LoginRequest
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["Authentication & Creator Management"])

@router.post("/register")
def register_user(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(DBUser).filter(DBUser.username == req.username).first():
        raise HTTPException(status_code=400, detail="Username already exists. Choose another.")
    
    new_user = DBUser(
        username=req.username,
        password_hash=hash_password(req.password),
        full_name=req.username,
        role="patient"
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {
        "message": "User registered successfully", 
        "user_id": new_user.id, 
        "username": new_user.username
    }

@router.post("/login")
def login_user(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {
        "message": "Login successful", 
        "user_id": user.id, 
        "username": user.username, 
        "role": user.role
    }

# ==================== CREATOR INSPECTION CONTROLS ====================

def parse_extracted_data(raw_json_str: str):
    """Safely converts stored JSON strings into dictionary format for clean API display."""
    if not raw_json_str:
        return None
    try:
        return json.loads(raw_json_str)
    except Exception:
        return raw_json_str

@router.get("/creator/all-data")
def get_all_patients_data(db: Session = Depends(get_db)):
    """Creator view: Fetches all registered users along with all their raw inputs, 
    scanned files, audio uploads, and complete AI extractions across the entire database."""
    users = db.query(DBUser).order_by(DBUser.created_at.desc()).all()
    results = []

    for u in users:
        sessions = (
            db.query(DBIntakeSession)
            .filter(DBIntakeSession.user_id == u.id)
            .order_by(DBIntakeSession.created_at.asc())
            .all()
        )
        results.append({
            "user_id": u.id,
            "username": u.username,
            "registered_at": u.created_at,
            "total_intakes": len(sessions),
            "records": [
                {
                    "session_id": s.id,
                    "input_type": s.source_type,  # text, chat, document_scan, audio
                    "target_language": s.target_language,
                    "customer_raw_input": s.raw_input,  # Shows exact text typed, audio file name, or scan uploaded
                    "chief_complaint": s.chief_complaint,
                    "full_extracted_clinical_json": parse_extracted_data(s.extracted_data_json),
                    "doctor_summary": s.concise_doctor_summary,
                    "submitted_at": s.created_at
                }
                for s in sessions
            ]
        })

    return results

@router.get("/creator/user/{user_id}")
def get_single_user_full_data(user_id: int, db: Session = Depends(get_db)):
    """Creator view: Fetches the complete raw inputs, scans, audio notes, 
    and extracted clinical profiles for a single specific user ID."""
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )

    return {
        "user_id": user.id,
        "username": user.username,
        "registered_at": user.created_at,
        "total_intakes": len(sessions),
        "intake_records": [
            {
                "session_id": s.id,
                "input_type": s.source_type,  # text, chat, document_scan, audio
                "target_language": s.target_language,
                "customer_raw_input": s.raw_input,  # Exactly what the customer sent
                "chief_complaint": s.chief_complaint,
                "full_extracted_clinical_json": parse_extracted_data(s.extracted_data_json),
                "doctor_summary": s.concise_doctor_summary,
                "submitted_at": s.created_at
            }
            for s in sessions
        ]
    }

@router.delete("/creator/user/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """Creator action: Permanently deletes a user and purges all their session records."""
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    db.query(DBIntakeSession).filter(DBIntakeSession.user_id == user_id).delete()
    db.delete(user)
    db.commit()
    return {"message": f"User {user.username} (ID: {user_id}) and all intake records permanently deleted."}
