from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import DBUser, DBIntakeSession
from app.schemas import RegisterRequest, LoginRequest
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["Authentication & Creator Admin"])

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

# ==================== CREATOR CONTROLS ====================

@router.get("/users")
def get_all_users(db: Session = Depends(get_db)):
    """Creator view: lists every registered user."""
    users = db.query(DBUser).order_by(DBUser.created_at.desc()).all()
    return [
        {
            "user_id": u.id,
            "username": u.username,
            "role": u.role,
            "registered_at": u.created_at
        }
        for u in users
    ]

@router.get("/users/{user_id}")
def get_user_details(user_id: int, db: Session = Depends(get_db)):
    """Creator view: inspects all historical files and clinical data for a user."""
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
        "intake_history": [
            {
                "session_id": s.id,
                "source_type": s.source_type,
                "target_language": s.target_language,
                "chief_complaint": s.chief_complaint,
                "summary": s.concise_doctor_summary,
                "timestamp": s.created_at
            }
            for s in sessions
        ]
    }

@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """Creator action: permanently deletes a user and wipes all their session records."""
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    db.query(DBIntakeSession).filter(DBIntakeSession.user_id == user_id).delete()
    db.delete(user)
    db.commit()
    return {"message": f"User {user.username} (ID: {user_id}) and all intake records permanently deleted."}
