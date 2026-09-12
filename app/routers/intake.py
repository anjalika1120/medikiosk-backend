import io
import json
from typing import List
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from pypdf import PdfReader
from google.genai import types

from app.database import get_db
from app.models import DBUser, DBIntakeSession
from app.schemas import TextIntakeRequest
from app.gemini_service import compile_patient_history, generate_robust

router = APIRouter(prefix="/api", tags=["Intake & Sessions"])

@router.post("/intake/text")
def process_text_intake(req: TextIntakeRequest, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User ID not found")

    prior_history = compile_patient_history(user.id, db)
    prompt = (
        f"PATIENT PRIOR HISTORY:\n{prior_history}\n\n"
        f"NEW INCOMING ENTRY ({req.source_type.upper()}):\n{req.content}\n\n"
        f"Integrate this input with past records and output in target language: {req.target_language}."
    )

    response = generate_robust(prompt, target_language=req.target_language)
    data = json.loads(response.text)

    session_entry = DBIntakeSession(
        user_id=user.id,
        source_type=req.source_type,
        target_language=req.target_language,
        raw_input=req.content,
        chief_complaint=data.get("main_concern", "Text Intake"),
        extracted_data_json=response.text,
        concise_doctor_summary=data.get("concise_doctor_summary", "")
    )
    db.add(session_entry)
    db.commit()
    db.refresh(session_entry)

    return {
        "session_id": session_entry.id,
        "patient": user.full_name,
        "target_language": req.target_language,
        "cumulative_structured_data": data,
        "concise_doctor_summary": session_entry.concise_doctor_summary
    }

@router.post("/intake/documents")
async def process_document_scans(
    user_id: int = Form(...),
    target_language: str = Form("English"),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User ID not found")

    prior_history = compile_patient_history(user.id, db)
    multimodal_parts = []
    file_names = []

    for f in files:
        file_names.append(f.filename)
        file_bytes = await f.read()
        mime = f.content_type or "image/png"

        if "pdf" in mime or f.filename.endswith(".pdf"):
            try:
                pdf_reader = PdfReader(io.BytesIO(file_bytes))
                extracted_pdf_text = "\n".join([page.extract_text() or "" for page in pdf_reader.pages])
                multimodal_parts.append(f"\n[Uploaded PDF: {f.filename}]\n{extracted_pdf_text}")
            except Exception:
                multimodal_parts.append(types.Part.from_bytes(data=file_bytes, mime_type="application/pdf"))
        else:
            multimodal_parts.append(types.Part.from_bytes(data=file_bytes, mime_type=mime))

    prompt = (
        f"PATIENT PRIOR HISTORY:\n{prior_history}\n\n"
        f"NEW INCOMING DOCUMENTS:\n"
        f"Extract all clinical data, lab values, and prescription text from these scans/PDFs. "
        f"Support multilingual text (including Hindi Devanagari and English). "
        f"Synthesize with prior patient history and convert the structured output and summary into {target_language}."
    )
    multimodal_parts.append(prompt)

    response = generate_robust(multimodal_parts, target_language=target_language)
    data = json.loads(response.text)

    session_entry = DBIntakeSession(
        user_id=user.id,
        source_type="document_scan",
        target_language=target_language,
        raw_input=f"Files uploaded: {', '.join(file_names)}",
        chief_complaint=data.get("main_concern", "Prescription/Report OCR"),
        extracted_data_json=response.text,
        concise_doctor_summary=data.get("concise_doctor_summary", "")
    )
    db.add(session_entry)
    db.commit()
    db.refresh(session_entry)

    return {
        "session_id": session_entry.id,
        "patient": user.full_name,
        "files_analyzed": file_names,
        "target_language": target_language,
        "cumulative_structured_data": data,
        "concise_doctor_summary": session_entry.concise_doctor_summary
    }

@router.post("/intake/audio")
async def process_audio_intake(
    user_id: int = Form(...),
    target_language: str = Form("English"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User ID not found")

    audio_bytes = await file.read()
    mime = file.content_type or "audio/mp3"
    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime)

    prior_history = compile_patient_history(user.id, db)
    prompt = (
        f"PATIENT PRIOR HISTORY:\n{prior_history}\n\n"
        f"Transcribe this clinical audio recording (spoken in Hindi, Hinglish, English, or regional dialects). "
        f"Extract new symptoms and clinical notes, synthesize with all previous records, "
        f"and return the combined clinical record and doctor summary translated into {target_language}."
    )

    response = generate_robust([audio_part, prompt], target_language=target_language)
    data = json.loads(response.text)

    session_entry = DBIntakeSession(
        user_id=user.id,
        source_type="audio",
        target_language=target_language,
        raw_input=f"Audio intake: {file.filename}",
        chief_complaint=data.get("main_concern", "Audio Clinical Intake"),
        extracted_data_json=response.text,
        concise_doctor_summary=data.get("concise_doctor_summary", "")
    )
    db.add(session_entry)
    db.commit()
    db.refresh(session_entry)

    return {
        "session_id": session_entry.id,
        "patient": user.full_name,
        "target_language": target_language,
        "cumulative_structured_data": data,
        "concise_doctor_summary": session_entry.concise_doctor_summary
    }

@router.get("/sessions/{user_id}")
def get_user_sessions(user_id: int, db: Session = Depends(get_db)):
    sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )
    return [
        {
            "session_id": s.id,
            "source_type": s.source_type,
            "target_language": s.target_language,
            "chief_complaint": s.chief_complaint,
            "cumulative_summary": s.concise_doctor_summary,
            "timestamp": s.created_at
        }
        for s in sessions
    ]
