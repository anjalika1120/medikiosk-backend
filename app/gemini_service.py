import os
import time
from sqlalchemy.orm import Session
from google import genai
from google.genai import types
from app.models import DBIntakeSession

# Load primary and secondary API keys from environment
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY", ""),
    os.getenv("GEMINI_BACKUP_KEY_1", ""),
    os.getenv("GEMINI_BACKUP_KEY_2", "")
]
valid_keys = [k for k in GEMINI_API_KEYS if k.strip()]

# Model cascade hierarchy (tries latest first, then falls back)
CANDIDATE_MODELS = [
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

def get_gemini_client(key_index: int = 0) -> genai.Client:
    """Returns a Gemini Client using key rotation across available tokens."""
    if not valid_keys:
        # Defaults to default environment resolution if no explicit array keys
        return genai.Client()
    selected_key = valid_keys[key_index % len(valid_keys)]
    return genai.Client(api_key=selected_key)

def build_system_instruction(target_language: str = "English") -> str:
    """Constructs dynamic clinical synthesis instructions enforcing the target language."""
    return f"""
You are an expert multilingual clinical intake AI synthesizer.
Inputs may come in English, Hindi, Hinglish, regional scripts, scanned handwritten prescriptions, PDFs, or audio recordings.

Tasks:
1. Combine all historical patient records with incoming data into a single coherent file.
2. Deduplicate and merge all medications, dosages, and allergies across previous and current records.
3. Consolidate the symptom timeline continuously.
4. Translate and present the extracted clinical values and doctor summary into the patient's chosen TARGET LANGUAGE: {target_language}.
5. The 'concise_doctor_summary' must capture the complete visit history to date in EXACTLY TWO SENTENCES written in {target_language}.

Return STRICT JSON matching this schema:
{{
  "main_concern": "Primary medical issue in {target_language}",
  "symptom_timeline": "Consolidated chronological timeline in {target_language}",
  "allergies": ["list", "of", "allergies", "in {target_language}"],
  "medications_supplements": ["list", "of", "medications", "with", "dosages", "in {target_language}"],
  "vital_signs_mentioned": ["list", "of", "vital", "signs"],
  "concise_doctor_summary": "Exactly two sentences summarizing the overall clinical status for the physician in {target_language}."
}}
Do not wrap output in markdown fences (no ```json). Output raw JSON only.
"""

def generate_robust(contents, target_language: str = "English", max_attempts: int = 3):
    """
    Executes content generation by rotating through both API keys and candidate models
    to prevent quota bottlenecks, model deprecation issues, or transient downtime.
    """
    last_error = None
    system_prompt = build_system_instruction(target_language)

    for model_name in CANDIDATE_MODELS:
        for attempt in range(max_attempts):
            try:
                client = get_gemini_client(attempt)
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json"
                    )
                )
                if response and response.text:
                    return response
            except Exception as e:
                last_error = e
                time.sleep(0.5)
                continue  # Try next key / attempt

    raise RuntimeError(f"All model endpoints and keys failed. Last error: {last_error}")

def compile_patient_history(user_id: int, db: Session) -> str:
    """Fetches and aggregates all prior intake records for a cumulative clinical prompt."""
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
            f"Record #{idx} [{s.source_type.upper()}] logged on {s.created_at.strftime('%Y-%m-%d %H:%M')}:\n"
            f"- Prior Chief Complaint: {s.chief_complaint}\n"
            f"- Extracted Clinical JSON: {s.extracted_data_json}\n"
            f"- Doctor Summary at that point: {s.concise_doctor_summary}\n"
        )
    return "\n---\n".join(history_blocks)
