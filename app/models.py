from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class DBUser(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=True)
    age = Column(Integer, nullable=True)
    gender = Column(String, nullable=True)  # Female, Male, Other, Prefer not to say
    password_hash = Column(String, nullable=True)
    hashed_password = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    sessions = relationship("DBIntakeSession", back_populates="user", cascade="all, delete-orphan")
    cases = relationship("DBHealthCase", back_populates="user", cascade="all, delete-orphan")


class DBHealthCase(Base):
    __tablename__ = "health_cases"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organ_name = Column(String, nullable=False)  # e.g., "Eyes", "Head", "Heart / Chest"
    icon = Column(String, default="🩺")          # e.g., "👁️", "👤", "❤️"
    short_description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("DBUser", back_populates="cases")


class DBIntakeSession(Base):
    __tablename__ = "intake_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("health_cases.id"), nullable=True)
    source_type = Column(String, nullable=False)  # "description", "chat", "document_scan", "audio"
    target_language = Column(String, default="English")
    raw_input = Column(Text, nullable=True)
    chief_complaint = Column(String, nullable=True)
    extracted_data_json = Column(Text, nullable=True)
    concise_doctor_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("DBUser", back_populates="sessions")
