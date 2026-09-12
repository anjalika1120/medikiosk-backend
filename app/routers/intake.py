import io
import json
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
    """Fetches all prior sessions and formats them as structured clinical context."""
    prior_sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )

    if not prior_sessions:
        return "No prior medical history recorded for this patient."

    history_lines = []
    for s in prior_sessions:
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
# 3. Text Intake Endpoint
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

    gemini_result = await process_clinical_intake(
        input_type=req.source_type,
        raw_text=req.content,
        history_context=history_context,
        target_language=req.target_language
    )

    complaints = gemini_result.get("chief_complaints_cumulative", [])
    primary_complaint = complaints[0].get("symptom", "Not specified") if complaints else "Not specified"
    summary = gemini_result.get("clinical_summary_for_doctor", "")

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

    return build_doctor_triage_payload(
        db=db,
        user_id=req.user_id,
        current_session=new_session,
        gemini_data=gemini_result,
        target_language=req.target_language
    )


# ==========================================
# 4. Document / Image (OCR) Upload Endpoint
# ==========================================
@router.post("/documents")
async def process_document_intake(
    user_id: int = Form(...),
    target_language: str = Form("English"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {user_id} does not exist."
        )

    file_bytes = await file.read()
    content_type = file.content_type or ""
    extracted_text_chunks = []
    image_parts = []

    # Handle PDF
    if content_type == "application/pdf" or file.filename.lower().endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(file_bytes))
            pdf_text = "\n".join([page.extract_text() or "" for page in reader.pages])
            extracted_text_chunks.append(f"[Document: {file.filename}]\n{pdf_text}")
        except Exception as e:
            extracted_text_chunks.append(f"[Error reading PDF: {str(e)}]")

    # Handle Image
    elif content_type.startswith("image/") or file.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        image_parts.append({
            "mime_type": content_type if content_type.startswith("image/") else "image/jpeg",
            "data": file_bytes
        })
    else:
        extracted_text_chunks.append(f"[Unsupported file type: {file.filename}]")

    combined_text = "\n\n".join(extracted_text_chunks)
    history_context = get_patient_history_context(db, user_id)

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
        raw_input=f"Uploaded File: {file.filename}\n{combined_text}".strip(),
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
# 5. Audio Intake Endpoint
# ==========================================

@router.post("/audio")
async def process_audio(
    user_id: int = Form(..., description="ID of the registered patient"),
    target_language: str = Form("English"),
    audio_file: UploadFile = File(..., description="Upload audio file (MP3, WAV, M4A)"),
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
# 6. FEATURE 2: Get a Patient's Complete Dossier & Summary via user_id
# ==========================================

@router.get("/patient-dossier/{user_id}")
def get_patient_dossier(user_id: int, db: Session = Depends(get_db)):
    """
    Returns complete data for a specific patient:
    - User account info
    - Total sessions & chronological timeline
    - Overall cumulative doctor summary synthesized across all visits
    - Full extracted clinical findings for every session
    """
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient with ID {user_id} does not exist."
        )

    sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )

    timeline = []
    session_details = []
    all_summaries = []

    for s in sessions:
        parsed_data = {}
        if s.extracted_data_json:
            try:
                parsed_data = json.loads(s.extracted_data_json)
            except Exception:
                parsed_data = {"raw_fallback": s.extracted_data_json}

        dt_str = s.created_at.isoformat() if hasattr(s.created_at, "isoformat") else str(s.created_at)

        timeline.append({
            "session_id": s.id,
            "date_time": dt_str,
            "modality": s.source_type,
            "chief_complaint": s.chief_complaint
        })

        if s.concise_doctor_summary:
            all_summaries.append(f"[Session #{s.id} ({s.source_type})]: {s.concise_doctor_summary}")

        session_details.append({
            "session_id": s.id,
            "modality": s.source_type,
            "target_language": s.target_language,
            "chief_complaint": s.chief_complaint,
            "doctor_summary": s.concise_doctor_summary,
            "raw_input": s.raw_input,
            "extracted_clinical_record": parsed_data,
            "created_at": dt_str
        })

    # The most recent session carries the cumulative synthesis
    latest_clinical_record = session_details[-1]["extracted_clinical_record"] if session_details else {}
    latest_overall_summary = (
        session_details[-1]["doctor_summary"]
        if session_details
        else "No clinical sessions recorded yet."
    )

    return {
        "patient_profile": {
            "user_id": user.id,
            "username": user.username,
            "total_visits": len(sessions),
        },
        "cumulative_summary_for_doctor": latest_overall_summary,
        "all_session_summaries": all_summaries,
        "session_timeline": timeline,
        "active_clinical_state": {
            "allergies": latest_clinical_record.get("triage_priority_alerts", {}).get("critical_allergies", []),
            "chronic_conditions": latest_clinical_record.get("comprehensive_medical_history", {}).get("chronic_conditions", []),
            "current_medications": latest_clinical_record.get("comprehensive_medical_history", {}).get("current_medications", []),
            "latest_vitals": latest_clinical_record.get("vitals_reported", {})
        },
        "all_sessions_detailed": session_details
    }
