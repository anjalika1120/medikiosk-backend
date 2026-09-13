import io
import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Form, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session
from pypdf import PdfReader

from app.database import get_db
from app.models import DBUser, DBIntakeSession, DBHealthCase
from app.gemini_service import process_clinical_intake, process_audio_intake

router = APIRouter(prefix="/api/intake", tags=["Intake, Documents & Health Cases"])


class TextIntakeRequest(BaseModel):
    user_id: int
    case_id: Optional[int] = None
    source_type: str = "description"
    content: str
    target_language: str = "English"


class HealthCaseCreate(BaseModel):
    user_id: int
    organ_name: str
    icon: Optional[str] = "🩺"
    short_description: Optional[str] = None


class HealthCaseUpdate(BaseModel):
    organ_name: Optional[str] = None
    icon: Optional[str] = None
    short_description: Optional[str] = None


def get_patient_history_context(db: Session, user_id: int) -> str:
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
            "case_id": s.case_id,
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
            "case_id": current_session.case_id,
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


# Health Cases / Organ System Endpoints
@router.post("/cases")
def create_health_case(req: HealthCaseCreate, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    case = DBHealthCase(
        user_id=req.user_id,
        organ_name=req.organ_name,
        icon=req.icon or "🩺",
        short_description=req.short_description
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


@router.get("/cases/{user_id}")
def get_user_cases(user_id: int, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return db.query(DBHealthCase).filter(DBHealthCase.user_id == user_id).all()


@router.put("/cases/{case_id}")
def update_health_case(case_id: int, req: HealthCaseUpdate, db: Session = Depends(get_db)):
    case = db.query(DBHealthCase).filter(DBHealthCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    if req.organ_name is not None:
        case.organ_name = req.organ_name
    if req.icon is not None:
        case.icon = req.icon
    if req.short_description is not None:
        case.short_description = req.short_description

    db.commit()
    db.refresh(case)
    return case


@router.delete("/cases/{case_id}")
def delete_health_case(case_id: int, db: Session = Depends(get_db)):
    case = db.query(DBHealthCase).filter(DBHealthCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    db.delete(case)
    db.commit()
    return {"message": f"Case {case_id} deleted successfully"}


# Intake Endpoints
@router.post("/text")
async def process_text_intake(req: TextIntakeRequest, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User {req.user_id} does not exist.")

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
        case_id=req.case_id,
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


@router.post(
    "/documents",
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "user_id": {"type": "integer"},
                            "case_id": {"type": "integer", "nullable": True},
                            "target_language": {"type": "string", "default": "English"},
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                                "description": "Select up to 5 prescription/scan images or PDFs"
                            }
                        },
                        "required": ["user_id", "files"]
                    }
                }
            }
        }
    }
)
async def process_document_intake(
    user_id: int = Form(...),
    case_id: Optional[int] = Form(None),
    target_language: str = Form("English"),
    files: List[UploadFile] = File(..., description="Upload up to 5 medical documents or prescription images"),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} does not exist.")

    if not files or len(files) == 0:
        raise HTTPException(status_code=400, detail="No files were uploaded.")

    if len(files) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 files allowed per intake session.")

    extracted_text_chunks = []
    image_parts = []
    file_names = []

    for file in files:
        file_names.append(file.filename)
        content_type = file.content_type or ""
        file_bytes = await file.read()

        if content_type == "application/pdf" or file.filename.lower().endswith(".pdf"):
            try:
                reader = PdfReader(io.BytesIO(file_bytes))
                pdf_text = "\n".join([p.extract_text() or "" for p in reader.pages])
                extracted_text_chunks.append(f"[Document: {file.filename}]\n{pdf_text}")
            except Exception as e:
                extracted_text_chunks.append(f"[Error reading {file.filename}: {str(e)}]")
        elif content_type.startswith("image/") or file.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            image_parts.append({
                "mime_type": content_type if content_type.startswith("image/") else "image/jpeg",
                "data": file_bytes
            })
        else:
            extracted_text_chunks.append(f"[File: {file.filename}]")

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
    primary_complaint = complaints[0].get("symptom", "Not specified") if complaints else "Multi-Document Intake"
    summary = gemini_result.get("clinical_summary_for_doctor", "")

    new_session = DBIntakeSession(
        user_id=user_id,
        case_id=case_id,
        source_type="document_scan",
        target_language=target_language,
        raw_input=f"Files ({len(files)}): {', '.join(file_names)}\n{combined_text}".strip(),
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


@router.post("/audio")
async def process_audio(
    user_id: int = Form(...),
    case_id: Optional[int] = Form(None),
    target_language: str = Form("English"),
    audio_file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} does not exist.")

    audio_bytes = await audio_file.read()
    history_context = get_patient_history_context(db, user_id)

    mime_type = audio_file.content_type or "audio/mp3"
    gemini_result = await process_audio_intake(
        audio_bytes=audio_bytes,
        mime_type=mime_type,
        history_context=history_context,
        target_language=target_language
    )

    complaints = gemini_result.get("chief_complaints_cumulative", [])
    primary_complaint = complaints[0].get("symptom", "Not specified") if complaints else "Audio Intake"
    summary = gemini_result.get("clinical_summary_for_doctor", "")

    new_session = DBIntakeSession(
        user_id=user_id,
        case_id=case_id,
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


@router.get("/patient-dossier/{user_id}")
def get_patient_dossier(user_id: int, db: Session = Depends(get_db)):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"Patient {user_id} does not exist.")

    sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )

    cases = db.query(DBHealthCase).filter(DBHealthCase.user_id == user_id).all()

    timeline = []
    session_details = []
    all_summaries = []

    for s in sessions:
        parsed_data = {}
        if s.extracted_data_json:
            try:
                parsed_data = json.loads(s.extracted_data_json)
            except Exception:
                parsed_data = {"raw": s.extracted_data_json}

        dt_str = s.created_at.isoformat() if hasattr(s.created_at, "isoformat") else str(s.created_at)

        timeline.append({
            "session_id": s.id,
            "case_id": s.case_id,
            "date_time": dt_str,
            "modality": s.source_type,
            "chief_complaint": s.chief_complaint
        })

        if s.concise_doctor_summary:
            all_summaries.append(f"[Session #{s.id} ({s.source_type})]: {s.concise_doctor_summary}")

        session_details.append({
            "session_id": s.id,
            "case_id": s.case_id,
            "modality": s.source_type,
            "target_language": s.target_language,
            "chief_complaint": s.chief_complaint,
            "doctor_summary": s.concise_doctor_summary,
            "raw_input": s.raw_input,
            "extracted_clinical_record": parsed_data,
            "created_at": dt_str
        })

    latest_clinical_record = session_details[-1]["extracted_clinical_record"] if session_details else {}
    latest_overall_summary = (
        session_details[-1]["doctor_summary"] if session_details else "No clinical sessions recorded yet."
    )

    return {
        "patient_profile": {
            "user_id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "age": user.age,
            "gender": user.gender,
            "total_visits": len(sessions),
            "cases_count": len(cases)
        },
        "cases": cases,
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
