import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, engine, Base
from app.models import DBUser

Base.metadata.create_all(bind=engine)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


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
    if hasattr(user, "password_hash") and user.password_hash:
        return user.password_hash
    if hasattr(user, "hashed_password") and user.hashed_password:
        return user.hashed_password
    if hasattr(user, "password") and user.password:
        return user.password
    return ""


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(req: UserAuth, db: Session = Depends(get_db)):
    username_clean = req.username.strip()
    if not username_clean:
        raise HTTPException(status_code=400, detail="Username cannot be empty")

    if db.query(DBUser).filter(DBUser.username == username_clean).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    hashed = hash_pw(req.password)
    user = DBUser(username=username_clean)
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
        "message": "User successfully registered.",
        "user_id": user.id,
        "username": user.username
    }


@router.post("/login")
def login(req: UserAuth, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.username == req.username.strip()).first()
    if not user or not check_pw(req.password, get_user_hashed_pw(user)):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {"message": "Login successful", "user_id": user.id, "username": user.username}
