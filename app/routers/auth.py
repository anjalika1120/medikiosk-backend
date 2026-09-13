import logging
from typing import Optional
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, engine, Base
from app.models import DBUser

logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

router = APIRouter(prefix="/api/auth", tags=["Authentication & Profile"])


class UserRegister(BaseModel):
    full_name: Optional[str] = None
    username: str
    password: str
    age: Optional[int] = None
    gender: Optional[str] = None  # "Female", "Male", "Other", "Prefer not to say"


class UserLogin(BaseModel):
    username: str
    password: str


def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_pw(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def get_user_hashed_pw(user: DBUser) -> str:
    if hasattr(user, "password_hash") and user.password_hash:
        return user.password_hash
    if hasattr(user, "hashed_password") and user.hashed_password:
        return user.hashed_password
    return ""


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(req: UserRegister, db: Session = Depends(get_db)):
    try:
        username_clean = req.username.strip()
        if not username_clean:
            raise HTTPException(status_code=400, detail="Username cannot be empty")

        if db.query(DBUser).filter(DBUser.username == username_clean).first():
            raise HTTPException(status_code=400, detail="Username already exists")

        hashed = hash_pw(req.password)
        user = DBUser(
            username=username_clean,
            full_name=req.full_name,
            age=req.age,
            gender=req.gender
        )
        if hasattr(user, "password_hash"):
            user.password_hash = hashed
        elif hasattr(user, "hashed_password"):
            user.hashed_password = hashed
        else:
            setattr(user, "password_hash", hashed)

        db.add(user)
        db.commit()
        db.refresh(user)

        return {
            "message": "User registered successfully",
            "user_id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "age": user.age,
            "gender": user.gender
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Registration failed")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database or server error: {str(e)}")


@router.post("/login")
def login(req: UserLogin, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.username == req.username.strip()).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    stored_hash = get_user_hashed_pw(user)
    if not check_pw(req.password, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {
        "message": "Login successful",
        "user_id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "age": user.age,
        "gender": user.gender
    }
