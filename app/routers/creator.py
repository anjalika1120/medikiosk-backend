import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DBUser, DBIntakeSession

router = APIRouter(prefix="/api/creator", tags=["Creator & Doctor Clinical Views"])


@router.get("/all-patients")
def get_all_patients_summary(db: Session = Depends(get_db)):
    """Creator view: lists all patients and high-level summaries."""
    users = db.query(DBUser).order_by(DBUser.id.asc()).all()
    dossiers = []

    for u in users:
        sessions = (
            db.query(DBIntakeSession)
            .filter(DBIntakeSession.user_id == u.id)
            .order_by(DBIntakeSession.created_at.asc())
            .all()
        )

        latest_summary = sessions[-1].concise_doctor_summary if sessions else "No intake records yet."
        latest_complaint = sessions[-1].chief_complaint if sessions else "N/A"

        dossiers.append({
            "user_id": u.id,
            "username": u.username,
            "full_name": u.full_name,
            "age": u.age,
            "gender": u.gender,
            "registered_at": u.created_at.isoformat() if hasattr(u.created_at, "isoformat") else str(u.created_at),
            "total_visits": len(sessions),
            "latest_chief_complaint": latest_complaint,
            "latest_clinical_summary": latest_summary,
            "session_ids": [s.id for s in sessions]
        })

    return {
        "total_patients": len(users),
        "patients": dossiers
    }


@router.get("/patient/{user_id}")
def get_patient_complete_data(user_id: int, db: Session = Depends(get_db)):
    """Creator view: inspects all historical records for a patient."""
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"Patient with ID {user_id} does not exist.")

    sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )

    session_list = []
    summaries = []

    for s in sessions:
        parsed_data = {}
        if s.extracted_data_json:
            try:
                parsed_data = json.loads(s.extracted_data_json)
            except Exception:
                parsed_data = {"raw_text": s.extracted_data_json}

        dt_str = s.created_at.isoformat() if hasattr(s.created_at, "isoformat") else str(s.created_at)

        if s.concise_doctor_summary:
            summaries.append(f"Session {s.id} ({s.source_type}): {s.concise_doctor_summary}")

        session_list.append({
            "session_id": s.id,
            "case_id": s.case_id,
            "source_type": s.source_type,
            "target_language": s.target_language,
            "chief_complaint": s.chief_complaint,
            "doctor_summary": s.concise_doctor_summary,
            "raw_patient_input": s.raw_input,
            "extracted_clinical_json": parsed_data,
            "created_at": dt_str
        })

    overall_summary = sessions[-1].concise_doctor_summary if sessions else "No sessions recorded."

    return {
        "user_id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "age": user.age,
        "gender": user.gender,
        "total_sessions": len(sessions),
        "overall_cumulative_doctor_summary": overall_summary,
        "session_by_session_summaries": summaries,
        "sessions": session_list
    }


@router.get("/doctor-summary-view/{user_id}")
def get_doctor_quick_summary(user_id: int, db: Session = Depends(get_db)):
    """
    Dedicated 1-minute Doctor Consultation View:
    Synthesizes patient demographics, critical allergies/red flags, cumulative doctor summary,
    active medication profile, vitals, and encounter timeline.
    """
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Patient not found")

    sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )

    if not sessions:
        return {
            "patient_header": {
                "patient_id": user.id,
                "full_name": user.full_name or user.username,
                "username": user.username,
                "age": user.age,
                "gender": user.gender,
                "total_visits": 0
            },
            "status": "No intake sessions recorded yet."
        }

    latest_session = sessions[-1]
    latest_data = {}
    if latest_session.extracted_data_json:
        try:
            latest_data = json.loads(latest_session.extracted_data_json)
        except Exception:
            latest_data = {}

    encounters = []
    for s in sessions:
        dt_str = s.created_at.strftime("%b %d, %Y - %I:%M %p") if s.created_at else "N/A"
        encounters.append({
            "session_id": s.id,
            "date": dt_str,
            "modality": s.source_type,
            "chief_complaint": s.chief_complaint,
            "summary": s.concise_doctor_summary
        })

    return {
        "patient_header": {
            "patient_id": user.id,
            "full_name": user.full_name or user.username,
            "username": user.username,
            "age": user.age,
            "gender": user.gender,
            "total_visits": len(sessions),
            "latest_visit_date": encounters[-1]["date"]
        },
        "critical_priority_alerts": {
            "allergies": latest_data.get("triage_priority_alerts", {}).get("critical_allergies", []),
            "red_flags": latest_data.get("triage_priority_alerts", {}).get("red_flags", [])
        },
        "cumulative_ai_doctor_summary": latest_session.concise_doctor_summary or "No summary available.",
        "active_clinical_profile": {
            "chief_complaints_cumulative": latest_data.get("chief_complaints_cumulative", []),
            "history_of_present_illness": latest_data.get("history_of_present_illness", ""),
            "chronic_conditions": latest_data.get("comprehensive_medical_history", {}).get("chronic_conditions", []),
            "current_medications": latest_data.get("comprehensive_medical_history", {}).get("current_medications", []),
            "discontinued_medications": latest_data.get("comprehensive_medical_history", {}).get("discontinued_or_ineffective_medications", []),
            "full_allergy_registry": latest_data.get("comprehensive_medical_history", {}).get("allergies", []),
            "vitals_reported": latest_data.get("vitals_reported", {})
        },
        "historical_encounters_timeline": encounters
    }


@router.delete("/patient/{user_id}")
def delete_patient_by_id(user_id: int, db: Session = Depends(get_db)):
    """Deletes patient account and all related sessions."""
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"Patient with ID {user_id} does not exist.")

    deleted_sessions = db.query(DBIntakeSession).filter(DBIntakeSession.user_id == user_id).delete()
    db.delete(user)
    db.commit()

    return {
        "message": f"Patient ID {user_id} and all related records deleted.",
        "deleted_intake_sessions": deleted_sessions
    }


@router.delete("/reset-all")
def reset_all_patients(db: Session = Depends(get_db)):
    """Wipes all patient and session data."""
    sessions_count = db.query(DBIntakeSession).delete()
    users_count = db.query(DBUser).delete()
    db.commit()

    return {
        "message": "All database records have been deleted.",
        "total_users_deleted": users_count,
        "total_sessions_deleted": sessions_count
    }
