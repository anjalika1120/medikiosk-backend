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
# 1. Pydantic Schemas
# ==========================================

class TextIntakeRequest(BaseModel):
    user_id: int
    source_type: str = "description"  # "description" or "chat"
    content: str
    target_language: str = "English"


# ==========================================
# 2. Cumulative History & Payload Builders
# ==========================================

def get_patient_history_context(db: Session, user_id: int) -> str:
    """
    Fetches all historical records for the patient and aggregates past complaints,
    allergies, medications, and physician notes into clinical context for Gemini.
    """
    prior_sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )

    if not prior_sessions:
        return "No prior medical history recorded for this patient."

    history_lines = []
    for idx, s in enumerate(prior_sessions, 1):
        dt_str = s.created_at.isoformat() if hasattr(s.created_at, "isoformat") else str(s.created_at)
        history_lines.append(
            f"--- [Session #{s.id} | Date: {dt_str} | Modality: {s.source_type}] ---\n"
            f"Chief Complaint: {s.chief_complaint}\n"
            f"Prior Doctor Summary: {s.concise_doctor_summary}\n"
            f"Extracted Findings: {s.extracted_data_json}"
        )
    return "\n\n".join(history_lines)


def build_doctor_triage_payload(
    db: Session,
    user_id: int,
    current_session: DBIntakeSession,
    gemini_data: dict,
    target_language: str
) -> dict:
    """
    Assembles the unified gold-standard clinical JSON schema combining database
    session history and Gemini clinical extractions.
    """
    all_sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )

    timeline = [
        {
            "session_id": s.id,
            "date_time": s.created_at.isoformat() if hasattr(s.created_at, "isoformat") else str(s.created_at),
            "modality": s.source_type,
            "key_event": s.chief_complaint
        }
        for s in all_sessions
    ]

    return {
        "doctor_session_view": {
            "current_session_id": current_session.id,
            "user_id": user_id,
            "total_historical_sessions": len(all_sessions),
            "session_timeline": timeline
        },
        "triage_priority_alerts": gemini_data.get("triage_priority_alerts", {
            "critical_allergies": [],
            "red_flags": []
        }),
        "patient_demographics": gemini_data.get("patient_demographics", {
            "name": None,
            "age": None,
            "gender": None
        }),
        "chief_complaints_cumulative": gemini_data.get("chief_complaints_cumulative", []),
        "history_of_present_illness": gemini_data.get("history_of_present_illness", ""),
        "comprehensive_medical_history": gemini_data.get("comprehensive_medical_history", {
            "chronic_conditions": [],
            "past_surgeries_hospitalizations": [],
            "current_medications": [],
            "discontinued_or_ineffective_medications": [],
            "allergies": [],
            "family_history": []
        }),
        "vitals_reported": gemini_data.get("vitals_reported", {
            "temperature": None,
            "blood_pressure": None,
            "heart_rate": None,
            "blood_sugar": None,
            "oxygen_saturation_spo2": None
        }),
        "clinical_summary_for_doctor": gemini_data.get(
            "clinical_summary_for_doctor",
            current_session.concise_doctor_summary
        )
    }


# ==========================================
# 3. Text & Dialogue Intake Endpoint
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

    # 1. Gather all prior patient history
    history_context = get_patient_history_context(db, req.user_id)

    # 2. Call Gemini clinical engine with cumulative context
    gemini_result = await process_clinical_intake(
        input_type=req.source_type,
        raw_text=req.content,
        history_context=history_context,
        target_language=req.target_language
    )

    # 3. Determine chief complaint and summary for quick indexing
    complaints = gemini_result.get("chief_complaints_cumulative", [])
    primary_complaint = complaints[0].get("symptom", "Not specified") if complaints else "Not specified"
    summary = gemini_result.get("clinical_summary_for_doctor", "")

    # 4. Persist to SQLite
    new_session = DBIntakeSession(
        user_id=req.user_id,
        source_type=req.source_type,
        target_language=req.target_language,
        raw_input=req.content,
        chief_complaint=primary_complaint,
        extracted_data_json=json.dumps(gemini_result),
        concise_doctor_summary=summary,
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    # 5. Return the unified doctor triage JSON
    return build_doctor_triage_payload(
        db=db,
        user_id=req.user_id,
        current_session=new_session,
        gemini_data=gemini_result,
        target_language=req.target_language
    )


# ==========================================
# 4. Document & Image (OCR) Intake Endpoint
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

    # Call Gemini clinical engine with multimodal parts & cumulative context
    gemini_result = await process_clinical_intake(
        input_type="document_scan",
        raw_text=combined_text,
        image_parts=image_parts,
        history_context=history_context,
        target_language=target_language
    )

    complaints = gemini_result.get("chief_complaints_cumulative", [])
    primary_complaint = complaints[0].get("symptom", "Not specified") if complaints else "Prescription / Document Intake"
    summary = gemini_result.get("clinical_summary_for_doctor", "")

    new_session = DBIntakeSession(
        user_id=user_id,
        source_type="document_scan",
        target_language=target_language,
        raw_input=f"Uploaded Files: {', '.join(file_names)}\n{combined_text}".strip(),
        chief_complaint=primary_complaint,
        extracted_data_json=json.dumps(gemini_result),
        concise_doctor_summary=summary,
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    return build_doctor_triage_payload(
        db=db,
        user_id=user_id,
        current_session=new_session,
        gemini_data=gemini_result,
        target_language=target_language
    )


# ==========================================
# 5. Audio Voice Intake Endpoint
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
        if audio_file.filename.lower().endswith(".wav"):
            mime_type = "audio/wav"
        elif audio_file.filename.lower().endswith(".m4a"):
            mime_type = "audio/m4a"
        else:
            mime_type = "audio/mp3"

    gemini_result = await process_audio_intake(
        audio_bytes=audio_bytes,
        mime_type=mime_type,
        history_context=history_context,
        target_language=target_language
    )

    complaints = gemini_result.get("chief_complaints_cumulative", [])
    primary_complaint = complaints[0].get("symptom", "Not specified") if complaints else "Audio Voice Intake"
    summary = gemini_result.get("clinical_summary_for_doctor", "")

    new_session = DBIntakeSession(
        user_id=user_id,
        source_type="audio",
        target_language=target_language,
        raw_input=f"Audio Recording: {audio_file.filename}",
        chief_complaint=primary_complaint,
        extracted_data_json=json.dumps(gemini_result),
        concise_doctor_summary=summary,
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    return build_doctor_triage_payload(
        db=db,
        user_id=user_id,
        current_session=new_session,
        gemini_data=gemini_result,
        target_language=target_language
    )


# ==========================================
# 6. Patient Chronological History Endpoint
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

    results = []
    for s in sessions:
        parsed_data = {}
        if s.extracted_data_json:
            try:
                parsed_data = json.loads(s.extracted_data_json)
            except Exception:
                parsed_data = {}

        results.append({
            "session_id": s.id,
            "source_type": s.source_type,
            "target_language": s.target_language,
            "chief_complaint": s.chief_complaint,
            "concise_doctor_summary": s.concise_doctor_summary,
            "extracted_clinical_record": parsed_data,
            "created_at": s.created_at
        })

    return results
                        
