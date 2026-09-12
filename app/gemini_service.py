import os
import json
import logging
from typing import List, Optional, Dict, Any
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# 1. API Keys Pool (Reads all 3 keys)
# -------------------------------------------------------------
RAW_KEYS = [
    os.getenv("GEMINI_API_KEY"),
    os.getenv("GEMINI_API_KEY_2") or os.getenv("GEMINI_BACKUP_KEY_1"),
    os.getenv("GEMINI_API_KEY_3") or os.getenv("GEMINI_BACKUP_KEY_2"),
]
API_KEYS = [k.strip() for k in RAW_KEYS if k and k.strip()]

# -------------------------------------------------------------
# 2. Model Hierarchy (Tier 1: 3.6 -> Tier 2: 2.5 -> Tier 3: 1.5)
# -------------------------------------------------------------
FALLBACK_MODELS = [
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-1.5-flash"
]

CLINICAL_SYSTEM_INSTRUCTION = """
You are an expert clinical medical intake intelligence engine.
Your task is to analyze patient intake data (descriptions, doctor-patient dialogues, scanned documents/prescriptions, or audio recordings) alongside ALL PRIOR HISTORICAL SESSIONS for this patient.

CRITICAL INSTRUCTIONS:
1. CUMULATIVE MEDICAL MEMORY:
   - Carry forward all past allergies, chronic conditions, and ongoing medications from the prior history.
   - If a patient reported an allergy in an earlier session, it MUST still appear under triage_priority_alerts.critical_allergies.
   - Contrast new symptoms with prior symptoms to reflect progression (resolved vs newly developed).

2. DOCTOR SUMMARY:
   - Write a 2-3 sentence clinical summary for the attending doctor.
   - The summary MUST explicitly synthesize prior session records with current session updates.

3. SCHEMA COMPLIANCE:
   - Output ONLY valid JSON adhering strictly to this structure:

{
  "triage_priority_alerts": {
    "critical_allergies": [
      {
        "substance": "string",
        "severity": "string",
        "status": "string"
      }
    ],
    "red_flags": ["string"]
  },
  "patient_demographics": {
    "name": "string or null",
    "age": "integer or null",
    "gender": "string or null"
  },
  "chief_complaints_cumulative": [
    {
      "symptom": "string",
      "duration": "string",
      "severity": "string",
      "aggravating_factors": "string or null"
    }
  ],
  "history_of_present_illness": "string",
  "comprehensive_medical_history": {
    "chronic_conditions": ["string"],
    "past_surgeries_hospitalizations": ["string"],
    "current_medications": [
      {
        "medication": "string",
        "dosage": "string",
        "frequency": "string",
        "source": "string",
        "status": "string"
      }
    ],
    "discontinued_or_ineffective_medications": [
      {
        "medication": "string",
        "reason": "string"
      }
    ],
    "allergies": [
      {
        "allergen": "string",
        "reaction": "string",
        "contraindicated_classes": ["string"]
      }
    ],
    "family_history": ["string"]
  },
  "vitals_reported": {
    "temperature": "string or null",
    "blood_pressure": "string or null",
    "heart_rate": "string or null",
    "blood_sugar": "string or null",
    "oxygen_saturation_spo2": "string or null"
  },
  "clinical_summary_for_doctor": "string"
}
"""


async def execute_gemini_with_failover(contents: list) -> dict:
    """
    Cycles across all 3 API keys and model tiers (3.6 -> 2.5 -> 1.5)
    to handle rate limits or regional quota caps.
    """
    if not API_KEYS:
        raise ValueError("No Gemini API keys found. Please set GEMINI_API_KEY.")

    last_error = None

    # Outer loop: Try models from highest capability down to 1.5
    for model_name in FALLBACK_MODELS:
        # Inner loop: Try each API key for the current model
        for key_idx, key in enumerate(API_KEYS, start=1):
            try:
                client = genai.Client(api_key=key)
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=CLINICAL_SYSTEM_INSTRUCTION,
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )

                raw_text = response.text.strip()
                # Clean any stray markdown formatting if present
                if raw_text.startswith("```"):
                    raw_text = raw_text.split("\n", 1)[-1].rsplit("\n", 1)[0].strip()

                return json.loads(raw_text)

            except Exception as e:
                logger.warning(
                    f"Failover trigger: Model '{model_name}' with API Key #{key_idx} failed: {e}"
                )
                last_error = e
                continue

    raise RuntimeError(
        f"All 3 API keys and all model tiers (3.6, 2.5, 1.5) failed. Last error: {last_error}"
    )


async def process_clinical_intake(
    input_type: str,
    raw_text: str = "",
    image_parts: Optional[List[Dict[str, Any]]] = None,
    history_context: str = "",
    target_language: str = "English"
) -> dict:
    contents = []

    prompt = f"""
TARGET LANGUAGE FOR OUTPUT: {target_language}

HISTORICAL INTAKE CONTEXT (Previous visits/sessions):
{history_context}

CURRENT SESSION INTAKE ({input_type}):
{raw_text}
"""
    contents.append(prompt)

    if image_parts:
        for img in image_parts:
            contents.append(
                types.Part.from_bytes(
                    data=img["data"],
                    mime_type=img["mime_type"]
                )
            )

    return await execute_gemini_with_failover(contents)


async def process_audio_intake(
    audio_bytes: bytes,
    mime_type: str = "audio/mp3",
    history_context: str = "",
    target_language: str = "English"
) -> dict:
    prompt = f"""
TARGET LANGUAGE FOR OUTPUT: {target_language}

HISTORICAL INTAKE CONTEXT (Previous visits/sessions):
{history_context}

Please transcribe this spoken patient audio, synthesize it with their prior medical history, and extract the clinical triage JSON according to the system instructions.
"""
    contents = [
        prompt,
        types.Part.from_bytes(
            data=audio_bytes,
            mime_type=mime_type
        )
    ]

    return await execute_gemini_with_failover(contents)
