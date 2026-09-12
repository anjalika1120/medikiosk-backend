import json
from typing import List, Optional
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DBUser, DBIntakeSession

router = APIRouter(prefix="/api/auth", tags=["Authentication & Creator Management"])

# ==========================================
# 1. Native Bcrypt Utilities
# ==========================================

def hash_password(password: str) -> str:
    """Hashes a plaintext password using bcrypt with a salt."""
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plaintext password against the stored bcrypt hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False


# ==========================================
# 2. Pydantic Schemas
# ==========================================

class UserRegisterRequest(BaseModel):
    username: str
    password: str


class UserLoginRequest(BaseModel):
    username: str
    password: str


# ==========================================
# 3. Registration & Login Endpoints
# ==========================================

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(req: UserRegisterRequest, db: Session = Depends(get_db)):
    """
    Registers a new user/patient, hashes password with bcrypt,
    and stores account records in SQLite.
    """
    username_clean = req.username.strip()
    if not username_clean:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username cannot be empty."
        )

    # Check for existing user
    existing_user = db.query(DBUser).filter(DBUser.username == username_clean).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered."
        )

    hashed_pw = hash_password(req.password)

    new_user = DBUser(
        username=username_clean,
        hashed_password=hashed_pw
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    created_at_str = (
        new_user.created_at.isoformat()
        if hasattr(new_user.created_at, "isoformat")
        else str(new_user.created_at)
    )

    return {
        "message": "User successfully registered.",
        "user_id": new_user.id,
        "username": new_user.username,
        "created_at": created_at_str
    }


@router.post("/login")
def login_user(req: UserLoginRequest, db: Session = Depends(get_db)):
    """
    Authenticates patient/user credentials against stored bcrypt hashes.
    """
    user = db.query(DBUser).filter(DBUser.username == req.username.strip()).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password."
        )

    if not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password."
        )

    return {
        "message": "Authentication successful.",
        "user_id": user.id,
        "username": user.username
    }


# ==========================================
# 4. Creator / Admin Inspection Endpoints
# ==========================================

@router.get("/creator/user/{user_id}")
def get_user_intake_data(user_id: int, db: Session = Depends(get_db)):
    """
    Creator endpoint: Returns complete raw inputs, full extracted clinical JSON,
    and cumulative progression history for a specific patient.
    """
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} does not exist."
        )

    records = []
    sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )

    for s in sessions:
        parsed_data = {}
        if s.extracted_data_json:
            try:
                parsed_data = json.loads(s.extracted_data_json)
            except Exception:
                parsed_data = {"raw_fallback": s.extracted_data_json}

        submitted_str = (
            s.created_at.isoformat()
            if hasattr(s.created_at, "isoformat")
            else str(s.created_at)
        )

        records.append({
            "session_id": s.id,
            "input_type": s.source_type,
            "target_language": s.target_language,
            "customer_raw_input": s.raw_input,
            "chief_complaint": s.chief_complaint,
            "full_extracted_clinical_json": parsed_data,
            "doctor_summary": s.concise_doctor_summary,
            "submitted_at": submitted_str
        })

    user_reg_date = (
        user.created_at.isoformat()
        if hasattr(user.created_at, "isoformat")
        else str(user.created_at)
    )

    return {
        "user_id": user.id,
        "username": user.username,
        "registered_at": user_reg_date,
        "total_intakes": len(records),
        "intake_records": records
    }


@router.get("/creator/all-data")
def get_all_kiosk_data(db: Session = Depends(get_db)):
    """
    Creator endpoint: Exports the entire SQLite database content across all registered
    users and intake sessions for auditing.
    """
    users = db.query(DBUser).order_by(DBUser.id.asc()).all()
    results = []

    for u in users:
        sessions = (
            db.query(DBIntakeSession)
            .filter(DBIntakeSession.user_id == u.id)
            .order_by(DBIntakeSession.created_at.asc())
            .all()
        )

        session_list = []
        for s in sessions:
            try:
                parsed_data = json.loads(s.extracted_data_json) if s.extracted_data_json else {}
            except Exception:
                parsed_data = {"raw_fallback": s.extracted_data_json}

            session_list.append({
                "session_id": s.id,
                "input_type": s.source_type,
                "target_language": s.target_language,
                "raw_input": s.raw_input,
                "chief_complaint": s.chief_complaint,
                "extracted_clinical_json": parsed_data,
                "doctor_summary": s.concise_doctor_summary,
                "created_at": s.created_at.isoformat() if hasattr(s.created_at, "isoformat") else str(s.created_at)
            })

        results.append({
            "user_id": u.id,
            "username": u.username,
            "registered_at": u.created_at.isoformat() if hasattr(u.created_at, "isoformat") else str(u.created_at),
            "session_count": len(session_list),
            "sessions": session_list
        })

    return {
        "total_registered_users": len(users),
        "users": results
    }


# ==========================================
# 5. Creator / Admin Delete & Reset Endpoints
# ==========================================

@router.delete("/creator/user/{user_id}")
def delete_user_and_sessions(user_id: int, db: Session = Depends(get_db)):
    """
    Deletes a specific user and all associated intake sessions from the database.
    """
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} does not exist."
        )

    # Delete sessions first
    deleted_sessions_count = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .delete()
    )

    # Delete user and commit
    db.delete(user)
    db.commit()

    return {
        "message": f"User {user_id} ('{user.username}') and all linked records deleted successfully.",
        "deleted_sessions_count": deleted_sessions_count
    }


@router.delete("/creator/reset-all")
def reset_all_database_records(db: Session = Depends(get_db)):
    """
    Clears all intake sessions and users from the database.
    """
    sessions_deleted = db.query(DBIntakeSession).delete()
    users_deleted = db.query(DBUser).delete()
    db.commit()

    return {
        "message": "All kiosk intake records and user accounts cleared successfully.",
        "total_users_deleted": users_deleted,
        "total_sessions_deleted": sessions_deleted
    }
