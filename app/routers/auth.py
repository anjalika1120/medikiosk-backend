import json
import logging
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, engine, Base
from app.models import DBUser, DBIntakeSession

logger = logging.getLogger(__name__)

# Ensure tables are created
Base.metadata.create_all(bind=engine)

router = APIRouter(prefix="/api/auth", tags=["Authentication & Creator Management"])


class UserAuth(BaseModel):
    username: str
    password: str


def hash_pw(pw: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pw.encode('utf-8'), salt).decode('utf-8')


def check_pw(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False


def get_user_hashed_pw(user: DBUser) -> str:
    """Helper to read whichever password field is defined on DBUser."""
    if hasattr(user, "password_hash") and user.password_hash:
        return user.password_hash
    if hasattr(user, "hashed_password") and user.hashed_password:
        return user.hashed_password
    if hasattr(user, "password") and user.password:
        return user.password
    return ""


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(req: UserAuth, db: Session = Depends(get_db)):
    try:
        username_clean = req.username.strip()
        if not username_clean:
            raise HTTPException(status_code=400, detail="Username cannot be empty")

        existing_user = db.query(DBUser).filter(DBUser.username == username_clean).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Username already exists")

        hashed = hash_pw(req.password)
        
        # Instantiate DBUser dynamically matching your model's column name
        user = DBUser(username=username_clean)
        if hasattr(user, "password_hash"):
            user.password_hash = hashed
        elif hasattr(user, "hashed_password"):
            user.hashed_password = hashed
        elif hasattr(user, "password"):
            user.password = hashed
        else:
            # Fallback to direct attribute assignment
            setattr(user, "password_hash", hashed)

        db.add(user)
        db.commit()
        db.refresh(user)

        return {
            "message": "User successfully registered.",
            "user_id": user.id,
            "username": user.username
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Registration failed")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database or server error: {str(e)}")


@router.post("/login")
def login(req: UserAuth, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.username == req.username.strip()).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    stored_hash = get_user_hashed_pw(user)
    if not check_pw(req.password, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {"message": "Login successful", "user_id": user.id, "username": user.username}


@router.get("/creator/user/{user_id}")
def get_user_records(user_id: int, db: Session = Depends(get_db)):
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
        "sessions": [
            {
                "session_id": s.id,
                "modality": s.source_type,
                "chief_complaint": s.chief_complaint,
                "summary": s.concise_doctor_summary,
                "clinical_data": json.loads(s.extracted_data_json) if s.extracted_data_json else {}
            }
            for s in sessions
        ]
    }


@router.delete("/creator/user/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    db.query(DBIntakeSession).filter(DBIntakeSession.user_id == user_id).delete()
    db.delete(user)
    db.commit()
    return {"message": f"User {user_id} and records deleted"}


@router.delete("/creator/reset-all")
def reset_all(db: Session = Depends(get_db)):
    db.query(DBIntakeSession).delete()
    db.query(DBUser).delete()
    db.commit()
    return {"message": "All data wiped"}
