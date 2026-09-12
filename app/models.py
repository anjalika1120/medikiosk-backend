from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime
from app.database import Base

class DBUser(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    role = Column(String(20), default="patient")
    created_at = Column(DateTime, default=datetime.utcnow)

class DBIntakeSession(Base):
    __tablename__ = "intake_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    source_type = Column(String(30))  # "chat", "description", "document_scan", "audio"
    target_language = Column(String(30), default="English")
    raw_input = Column(Text, nullable=False)
    chief_complaint = Column(String(255))
    extracted_data_json = Column(Text)
    concise_doctor_summary = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

