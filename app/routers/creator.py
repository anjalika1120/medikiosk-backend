import os
import json
from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DBUser, DBIntakeSession

router = APIRouter(prefix="/api/creator", tags=["Creator / Admin (Protected)"])

# Set your creator secret here or in Render environment variables
CREATOR_SECRET = os.getenv("CREATOR_ADMIN_SECRET", "admin_medikiosk_secret_2026")


def verify_admin(x_admin_secret: str = Header(..., description="Creator Admin Authorization Key")):
    if x_admin_secret != CREATOR_SECRET:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Invalid Creator Admin Secret"
        )
    return True


# ==========================================
# 1st GET: All patients data & high-level summary
# ==========================================
@router.get("/all-patients")
def get_all_patients_summary(
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin)
):
    """
    Creator only: Fetches all registered patients with their session count,
    latest chief complaints, and historical summaries.
    """
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
            "registered_at": u.created_at.isoformat() if hasattr(u.created_at, "isoformat") else str(u.created_at),
            "total_visits": len(sessions),
            "latest_chief_complaint": latest_complaint,
            "latest_clinical_summary": latest_summary,
            "session_ids": [s.id for s in sessions]
        })

    return {
        "system_status": "authorized",
        "total_patients": len(users),
        "patients": dossiers
    }


# ==========================================
# 2nd GET: Complete data and summary for a specific patient via user_id
# ==========================================
@router.get("/patient/{user_id}")
def get_patient_complete_data(
    user_id: int,
    db: Session = Depends(get_db),
    authorized: bool = Depends(verify_admin)
):
    """
    Creator only: Inspects all historical intake records, full JSON schema outputs,
    doctor summaries, and raw customer inputs for a single user ID.
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
        "total_sessions": len(sessions),
        "overall_cumulative_doctor_summary": overall_summary,
        "session_by_session_summaries": summaries,
        "sessions": session_list
    }

