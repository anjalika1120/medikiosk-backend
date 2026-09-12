import json
import io
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Form, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session
from pypdf import PdfReader

from app.database import get_db
from app.models import DBUser, DBIntakeSession
from app.gemini_service import process_clinical_intake, process_audio_intake

router = APIRouter(prefix="/api/intake", tags=["Intake & Sessions"])


# ==========================================
# Pydantic Schemas
# ==========================================

class TextIntakeRequest(BaseModel):
    user_id: int
    source_type: str = "description"  # "description" or "chat"
    content: str
    target_language: str = "English"


# ==========================================
# Helper: Cumulative History Retrieval
# ==========================================

def get_patient_history_context(db: Session, user_id: int) -> str:
    """
    Fetches all previous intake records for the patient to maintain continuous context.
    """
    prior_sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )

    if not prior_sessions:
        return "No prior medical history available for this patient."

    history_lines = []
    for idx, s in enumerate(prior_sessions, 1):
        history_lines.append(
            f"--- Prior Record {idx} ({s.source_type} on {s.created_at}) ---\n"
            f"Chief Complaint: {s.chief_complaint}\n"
            f"Doctor Summary: {s.concise_doctor_summary}\n"
            f"Extracted Findings: {s.extracted_data_json}"
        )
    return "\n\n".join(history_lines)


# ==========================================
# 1. Text & Dialogue Intake Endpoint
# ==========================================

@router.post("/text")
async def process_text_intake(
    req: TextIntakeRequest,
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == req.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {req.user_id} does not exist."
        )

    history_context = get_patient_history_context(db, req.user_id)

    # Call Gemini clinical engine with cumulative context
    gemini_result = await process_clinical_intake(
        input_type=req.source_type,
        raw_text=req.content,
        history_context=history_context,
        target_language=req.target_language
    )

    # Persist session to SQLite database
    new_session = DBIntakeSession(
        user_id=req.user_id,
        source_type=req.source_type,
        target_language=req.target_language,
        raw_input=req.content,
        chief_complaint=gemini_result.get("chief_complaint", "Not specified"),
        extracted_data_json=json.dumps(gemini_result.get("extracted_data", {})),
        concise_doctor_summary=gemini_result.get("concise_doctor_summary", ""),
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    return {
        "session_id": new_session.id,
        "user_id": req.user_id,
        "source_type": req.source_type,
        "target_language": req.target_language,
        "chief_complaint": new_session.chief_complaint,
        "concise_doctor_summary": new_session.concise_doctor_summary,
        "extracted_data": gemini_result.get("extracted_data", {}),
        "created_at": new_session.created_at
    }


# ==========================================
# 2. Document & Image (OCR) Intake Endpoint
# ==========================================

@router.post("/documents")
async def process_document_intake(
    user_id: int = Form(...),
    target_language: str = Form("English"),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} does not exist."
        )

    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files were uploaded."
        )

    extracted_text_chunks = []
    image_parts = []
    file_names = []

    for file in files:
        file_names.append(file.filename)
        content_type = file.content_type or ""
        file_bytes = await file.read()

        # Handle PDF documents
        if content_type == "application/pdf" or file.filename.lower().endswith(".pdf"):
            try:
                reader = PdfReader(io.BytesIO(file_bytes))
                pdf_text = "\n".join([page.extract_text() or "" for page in reader.pages])
                extracted_text_chunks.append(f"[Document: {file.filename}]\n{pdf_text}")
            except Exception as e:
                extracted_text_chunks.append(f"[Error reading PDF {file.filename}: {str(e)}]")

        # Handle Image uploads (Prescriptions, Lab tests, Scans)
        elif content_type.startswith("image/") or file.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            image_parts.append({
                "mime_type": content_type if content_type.startswith("image/") else "image/jpeg",
                "data": file_bytes
            })
        else:
            extracted_text_chunks.append(f"[Unsupported file type: {file.filename}]")

    combined_text = "\n\n".join(extracted_text_chunks)
    history_context = get_patient_history_context(db, user_id)

    # Process images and parsed text with Gemini
    gemini_result = await process_clinical_intake(
        input_type="document_scan",
        raw_text=combined_text,
        image_parts=image_parts,
        history_context=history_context,
        target_language=target_language
    )

    # Record session
    new_session = DBIntakeSession(
        user_id=user_id,
        source_type="document_scan",
        target_language=target_language,
        raw_input=f"Uploaded Files: {', '.join(file_names)}\n{combined_text}".strip(),
        chief_complaint=gemini_result.get("chief_complaint", "Not specified"),
        extracted_data_json=json.dumps(gemini_result.get("extracted_data", {})),
        concise_doctor_summary=gemini_result.get("concise_doctor_summary", ""),
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    return {
        "session_id": new_session.id,
        "user_id": user_id,
        "source_type": "document_scan",
        "target_language": target_language,
        "processed_files": file_names,
        "chief_complaint": new_session.chief_complaint,
        "concise_doctor_summary": new_session.concise_doctor_summary,
        "extracted_data": gemini_result.get("extracted_data", {}),
        "created_at": new_session.created_at
    }


# ==========================================
# 3. Audio Voice Intake Endpoint
# ==========================================

@router.post("/audio")
async def process_audio(
    user_id: int = Form(...),
    target_language: str = Form("English"),
    audio_file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} does not exist."
        )

    audio_bytes = await audio_file.read()
    history_context = get_patient_history_context(db, user_id)

    mime_type = audio_file.content_type or "audio/mp3"
    if "octet-stream" in mime_type:
        if audio_file.filename.endswith(".wav"):
            mime_type = "audio/wav"
        elif audio_file.filename.endswith(".m4a"):
            mime_type = "audio/m4a"
        else:
            mime_type = "audio/mp3"

    gemini_result = await process_audio_intake(
        audio_bytes=audio_bytes,
        mime_type=mime_type,
        history_context=history_context,
        target_language=target_language
    )

    new_session = DBIntakeSession(
        user_id=user_id,
        source_type="audio",
        target_language=target_language,
        raw_input=f"Audio Recording: {audio_file.filename}",
        chief_complaint=gemini_result.get("chief_complaint", "Not specified"),
        extracted_data_json=json.dumps(gemini_result.get("extracted_data", {})),
        concise_doctor_summary=gemini_result.get("concise_doctor_summary", ""),
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    return {
        "session_id": new_session.id,
        "user_id": user_id,
        "source_type": "audio",
        "target_language": target_language,
        "audio_file": audio_file.filename,
        "chief_complaint": new_session.chief_complaint,
        "concise_doctor_summary": new_session.concise_doctor_summary,
        "extracted_data": gemini_result.get("extracted_data", {}),
        "created_at": new_session.created_at
    }


# ==========================================
# 4. Standard Patient Session History
# ==========================================

@router.get("/sessions/{user_id}")
def get_user_sessions(user_id: int, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} does not exist."
        )

    sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.desc())
        .all()
    )

    return [
        {
            "session_id": s.id,
            "source_type": s.source_type,
            "target_language": s.target_language,
            "chief_complaint": s.chief_complaint,
            "concise_doctor_summary": s.concise_doctor_summary,
            "extracted_data": json.loads(s.extracted_data_json) if s.extracted_data_json else {},
            "created_at": s.created_at
        }
        for s in sessions
    ]
                
