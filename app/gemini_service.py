import json
import os
from google import genai
from google.genai import types

# Setup client (with your key fallback)
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

CLINICAL_SYSTEM_INSTRUCTION = """
You are an expert clinical medical intelligence engine for hospital intake.
Your task is to analyze new patient inputs (text, doctor-patient dialogues, scanned documents/prescriptions, or audio transcriptions) alongside ALL PRIOR HISTORICAL SESSIONS for this patient.

CRITICAL INSTRUCTIONS:
1. CUMULATIVE SYNTHESIS: You must preserve all historical facts (allergies, prior complaints, past medications, vitals) across sessions. If an allergy or condition was mentioned in an earlier session, carry it forward into the active list.
2. SUMMARY COMPLETENESS: The `clinical_summary_for_doctor` MUST be concise (2-3 sentences) but MUST explicitly mention both prior session findings and the current session updates.
3. OUTPUT FORMAT: You MUST return ONLY valid JSON adhering strictly to this schema:

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
    "age": "integer, string, or null",
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

async def process_clinical_intake(
    input_type: str,
    raw_text: str = "",
    image_parts: list = None,
    history_context: str = "",
    target_language: str = "English"
) -> dict:
    contents = []
    
    prompt = f"""
TARGET LANGUAGE FOR OUTPUT: {target_language}

HISTORICAL INTAKE CONTEXT:
{history_context}

CURRENT SESSION INPUT ({input_type}):
{raw_text}
"""
    contents.append(prompt)

    # Attach images if uploaded
    if image_parts:
        for img in image_parts:
            contents.append(
                types.Part.from_bytes(
                    data=img["data"],
                    mime_type=img["mime_type"]
                )
            )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=CLINICAL_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            temperature=0.1
        )
    )

    try:
        return json.loads(response.text)
    except Exception:
        # Fallback if raw markdown wrapped
        cleaned = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)
