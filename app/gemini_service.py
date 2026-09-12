import os
import time
from sqlalchemy.orm import Session
from google import genai
from google.genai import types
from app.models import DBIntakeSession

GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY", ""),
    os.getenv("GEMINI_BACKUP_KEY_1", ""),
    os.getenv("GEMINI_BACKUP_KEY_2", "")
]
valid_keys = [k for k in GEMINI_API_KEYS if k]

def get_gemini_client(key_index: int = 0):
    if not valid_keys:
        return genai.Client()
    return genai.Client(api_key=valid_keys[key_index % len(valid_keys)])

def build_system_instruction(target_language: str) -> str:
    return f"""
You are an expert multilingual clinical intake AI synthesizer.
Inputs may come in English, Hindi, Hinglish, regional scripts, scanned handwritten prescriptions, PDFs, or audio recordings.

Tasks:
1. Combine all historical patient records with the incoming data into an integrated clinical record.
2. Deduplicate and merge all medications, dosages, and allergies across previous and current records.
3. Consolidate the symptom timeline continuously.
4. Translate and present the extracted values and the doctor summary into the patient's chosen TARGET LANGUAGE: {target_language}.
5. The 'concise_doctor_summary' must capture the complete visit history to date in EXACTLY TWO SENTENCES written in {target_language}.

Return STRICT JSON matching this schema:
{{
  "main_concern": "Primary medical issue in {target_language}",
  "symptom_timeline": "Consolidated timeline in {target_language}",
  "allergies": ["list", "of", "allergies", "in {target_language}"],
  "medications_supplements": ["list", "of", "medications", "in {target_language}"],
  "vital_signs_mentioned": ["list", "of", "vital", "signs"],
  "concise_doctor_summary": "Exactly two sentences summarizing the overall clinical picture in {target_language}."
}}
Do not wrap output in markdown fences (no ```json). Output raw JSON only.
"""

def generate_robust(contents, target_language: str = "English", max_attempts: int = 3):
    last_error = None
    system_prompt = build_system_instruction(target_language)
    
    for attempt in range(max_attempts):
        try:
            client = get_gemini_client(attempt)
            return client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json"
                )
            )
        except Exception as e:
            last_error = e
            time.sleep(1.0)
    raise last_error

def compile_patient_history(user_id: int, db: Session) -> str:
    past_sessions = (
        db.query(DBIntakeSession)
        .filter(DBIntakeSession.user_id == user_id)
        .order_by(DBIntakeSession.created_at.asc())
        .all()
    )
    if not past_sessions:
        return "No prior records. This is the patient's first intake."

    history_blocks = []
    for idx, s in enumerate(past_sessions, 1):
        history_blocks.append(
            f"Record #{idx} [{s.source_type.upper()}] logged at {s.created_at.strftime('%Y-%m-%d %H:%M')}:\n"
            f"- Prior Chief Complaint: {s.chief_complaint}\n"
            f"- Historical Extracted Data: {s.extracted_data_json}\n"
            f"- Doctor Summary at that point: {s.concise_doctor_summary}\n"
        )
    return "\n---\n".join(history_blocks)
