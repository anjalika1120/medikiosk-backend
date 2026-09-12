import os
import json
import time
from typing import List, Optional, Literal
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pwdlib import PasswordHash
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from datetime import datetime
from google import genai
from google.genai import types

# ----------------- Security & Password Hashing -----------------
pwd_hash = PasswordHash.recommended()

def hash_password(password: str) -> str:
    return pwd_hash.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_hash.verify(plain_password, hashed_password)

# ----------------- Database Setup (SQLite) -----------------
DATABASE_URL = "sqlite:///./medikiosk.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class DBUser(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    role = Column(String(20), default="patient")  # "patient", "doctor", "creator"
    created_at = Column(DateTime, default=datetime.utcnow)

class DBIntakeSession(Base):
    __tablename__ = "intake_sessions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    source_type = Column(String(20))  # "chat" or "ocr"
    raw_input = Column(Text, nullable=False)
    chief_complaint = Column(String(255))
    extracted_data_json = Column(Text)
    concise_doctor_summary = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ----------------- Gemini Setup & Failover -----------------
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY", ""),
    os.getenv("GEMINI_BACKUP_KEY_1", ""),
    os.getenv("GEMINI_BACKUP_KEY_2", "")
]
valid_keys = [k for k in GEMINI_API_KEYS if k]

def get_gemini_client(key_index: int = 0):
    if not valid_keys:
        return genai.Client()
    return genai.Client(api_key=valid_keys[key_index % len(valid_keys)])

SYSTEM_INSTRUCTION = """
You are an expert clinical intake AI engine. Analyze the provided clinical transcript or prescription image.
Return STRICT JSON matching these keys:
{
  "main_concern": "Primary symptom or reason for visit",
  "symptom_timeline": "When symptoms started and duration",
  "allergies": ["list", "of", "allergies"],
  "medications_supplements": ["list", "of", "medications"],
  "vital_signs_mentioned": ["any vitals stated"],
  "concise_doctor_summary": "Exactly two sentences summarizing clinical findings for the physician."
}
Do not wrap in markdown quotes. Output raw JSON only.
"""

def generate_robust(contents, max_attempts: int = 3):
    last_error = None
    for attempt in range(max_attempts):
        try:
            client = get_gemini_client(attempt)
            return client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json"
                )
            )
        except Exception as e:
            last_error = e
            time.sleep(1.0)
    raise last_error

# ----------------- FastAPI App -----------------
app = FastAPI(
    title="MediKiosk Backend API",
    description="Multimodal clinical intake, OCR, persistent SQLite, and doctor summary generation.",
    version="1.5.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- Pydantic Schemas -----------------
class RegisterRequest(BaseModel):
    username: str
    password: str
    full_name: str
    role: Optional[Literal["patient", "doctor", "creator"]] = "patient"

class LoginRequest(BaseModel):
    username: str
    password: str

class ChatIntakeRequest(BaseModel):
    user_id: int
    chat_dialogue: str

# ----------------- Endpoints -----------------
@app.get("/")
def health_check():
    return {"status": "active", "service": "MediKiosk API"}

@app.post("/api/auth/register")
def register_user(req: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(DBUser).filter(DBUser.username == req.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    new_user = DBUser(
        username=req.username,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        role=req.role
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "User registered successfully", "user_id": new_user.id, "role": new_user.role}

@app.post("/api/auth/login")
def login_user(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"message": "Login successful", "user_id": user.id, "full_name": user.full_name, "role": user.role}

@app.post("/api/intake/chat")
def process_chat_intake(req: ChatIntakeRequest, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    response = generate_robust(f"Extract clinical data from transcript:\n{req.chat_dialogue}")
    data = json.loads(response.text)
    
    session_entry = DBIntakeSession(
        user_id=user.id,
        source_type="chat",
        raw_input=req.chat_dialogue,
        chief_complaint=data.get("main_concern", "General Intake"),
        extracted_data_json=response.text,
        concise_doctor_summary=data.get("concise_doctor_summary", "")
    )
    db.add(session_entry)
    db.commit()
    db.refresh(session_entry)
    
    return {
        "session_id": session_entry.id,
        "patient": user.full_name,
        "structured_data": data,
        "concise_doctor_summary": session_entry.concise_doctor_summary
    }

@app.post("/api/intake/image")
async def process_image_ocr(user_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    image_bytes = await file.read()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=file.content_type or "image/png")

    response = generate_robust([
        image_part,
        "Read all printed and handwritten prescription or clinical text and extract structured findings."
    ])
    data = json.loads(response.text)

    session_entry = DBIntakeSession(
        user_id=user.id,
        source_type="ocr",
        raw_input=f"Uploaded file: {file.filename}",
        chief_complaint=data.get("main_concern", "Prescription OCR"),
        extracted_data_json=response.text,
        concise_doctor_summary=data.get("concise_doctor_summary", "")
    )
    db.add(session_entry)
    db.commit()
    db.refresh(session_entry)

    return {
        "session_id": session_entry.id,
        "patient": user.full_name,
        "structured_data": data,
        "concise_doctor_summary": session_entry.concise_doctor_summary
    }

@app.get("/api/sessions/{user_id}")
def get_user_sessions(user_id: int, db: Session = Depends(get_db)):
    sessions = db.query(DBIntakeSession).filter(DBIntakeSession.user_id == user_id).all()
    return [{"id": s.id, "type": s.source_type, "chief_complaint": s.chief_complaint, "summary": s.concise_doctor_summary, "date": s.created_at} for s in sessions]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
