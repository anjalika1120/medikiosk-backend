from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import DBUser
from app.schemas import RegisterRequest, LoginRequest
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

@router.post("/register")
def register_user(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(DBUser).filter(DBUser.username == req.username).first():
        raise HTTPException(status_code=400, detail="Username already exists. Choose another.")
    
    new_user = DBUser(
        username=req.username,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        role=req.role
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {
        "message": "User registered successfully", 
        "user_id": new_user.id, 
        "username": new_user.username, 
        "role": new_user.role
    }

@router.post("/login")
def login_user(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {
        "message": "Login successful", 
        "user_id": user.id, 
        "full_name": user.full_name, 
        "role": user.role
    }
