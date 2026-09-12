from typing import Optional, Literal
from pydantic import BaseModel, field_validator

class RegisterRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters.")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters.")
        return v

class LoginRequest(BaseModel):
    username: str
    password: str

class TextIntakeRequest(BaseModel):
    user_id: int
    source_type: Literal["chat", "description"]
    content: str
    target_language: Optional[str] = "English"
