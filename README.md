# 🏥 MediKiosk Universal Multilingual AI Clinical Intake Backend

[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-8E75C2?style=flat&logo=google&logoColor=white)](https://ai.google.dev)
[![SQLite](https://img.shields.io/badge/SQLite-07405E?style=flat&logo=sqlite&logoColor=white)](https://www.sqlite.org)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org)

An intelligent, multi-language clinical intake and triage backend engine built for automated hospital and clinic self-service kiosks. MediKiosk streamlines the emergency and outpatient triage workflow with multimodal symptom intake (text, multi-file PDFs, prescription scans, and recorded audio notes) paired with persistent **cumulative clinical memory** across repeat patient encounters.

---

## 🏛️ System Architecture

```text
+---------------------------------------------------------------------------------------+
|                                    CLIENT LAYER                                       |
|  Physical Kiosk Screen | Touchscreen Web UI | Multi-Language Patient & Doctor Portal  |
+---------------------------------------------------------------------------------------+
                                           |
                                           | HTTP / REST (JSON & Multipart Form-Data)
                                           v
+---------------------------------------------------------------------------------------+
|                                  FASTAPI APPLICATION                                  |
|                                                                                       |
|  +---------------------+   +-----------------------+   +---------------------------+  |
|  |   Auth & Profile    |   |   Intake & Cases      |   |     Creator / Doctor      |  |
|  |  (/api/auth)        |   |  (/api/intake)        |   |    (/api/creator)         |  |
|  |                     |   |                       |   |                           |  |
|  | - bcrypt hashing    |   | - Text / Chat intake  |   | - 1-Min Clinical Summary  |  |
|  | - User credentials  |   | - Up to 5 Scans/PDFs  |   | - All-Patients Registry   |  |
|  | - Demographics      |   | - Voice Audio Notes   |   | - History Inspection      |  |
|  |   (Age, Gender)     |   | - Organ Dossier CRUD  |   | - Reset / Data Wipe       |  |
|  +---------------------+   +-----------------------+   +---------------------------+  |
|                                        |                                              |
|                                        v                                              |
|                   +-----------------------------------------+                         |
|                   |        CUMULATIVE MEMORY ENGINE         |                         |
|                   |  - Aggregates past session timeline     |                         |
|                   |  - Injects active allergies & history   |                         |
|                   |  - Tracks symptom progression           |                         |
|                   +-----------------------------------------+                         |
+---------------------------------------------------------------------------------------+
          |                                                              |
          | Read / Write Records                                         | Failover Cascade
          v                                                              v
+-----------------------------+           +---------------------------------------------+
|    PERSISTENCE LAYER        |           |         INTELLIGENCE LAYER (Gemini)         |
|  SQLite (medikiosk.db)      |           |                                             |
|  SQLAlchemy ORM             |           |  API Key Pool:                              |
|                             |           |  [ Key 1 ] ---> [ Key 2 ] ---> [ Key 3 ]    |
|  - DBUser                   |           |                                             |
|  - DBHealthCase             |           |  Model Failover Cascade:                    |
|  - DBIntakeSession          |           |  gemini-3.6-flash                           |
|    (Raw Inputs, Summaries,  |           |        | (on rate-limit / quota failure)    |
|     Cumulative JSON Data)   |           |        v                                    |
+-----------------------------+           |  gemini-2.5-flash                           |
                                          |        |                                    |
                                          |        v                                    |
                                          |  gemini-1.5-flash                           |
                                          +---------------------------------------------+

```
---

## ⚡ Key Capabilities

* **Multimodal Intake Engine:**
  * **Text & Conversational Chat**: Accepts doctor-patient dialogues or patient symptom descriptions.
  * **Multi-File Medical Scans & PDFs**: Processes up to 5 files simultaneously (prescriptions, discharge summaries, lab scans) with PDF text extraction and vision OCR.
  * **Spoken Audio / Voice Notes**: Direct processing and transcription of audio intake (MP3, WAV, M4A).
* **Failover Matrix (3 API Keys × 3 Model Tiers):**
  * Automatic multi-key load balancing and rate-limit recovery.
  * Tiered cascade: `gemini-3.6-flash` → `gemini-2.5-flash` → `gemini-1.5-flash`.
* **Cumulative Clinical Memory:**
  * Aggregates historical sessions into ongoing context.
  * Persists critical allergies, chronic conditions, and active medications across completely distinct visits without requiring re-entry.
* **Organ & Health Case Dossiers:**
  * Matches interactive kiosk UI flows by organizing visits into body regions (Eyes, Head, Heart/Chest, etc.).
* **1-Minute Doctor Consultation Summary:**
  * High-density clinical snapshot synthesizing demographics, active contraindications, past vs. present complaint progression, vitals, and encounter history.
* **Persistent SQLite Storage:**
  * Every raw input, extracted JSON entity, timestamp, and clinical summary is stored in `medikiosk.db`.

---

## 🛠️ Tech Stack

* **Backend Framework:** FastAPI (Python 3.10+)
* **Database & ORM:** SQLite / SQLAlchemy
* **Authentication:** Password hashing via standard `bcrypt`
* **AI Intelligence Engine:** Google GenAI SDK (`google-genai`)
* **Document Processing:** `pypdf`, `python-multipart`

---

## 📂 Project Structure

```text
├── app/
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── auth.py              # User registration (demographics) & authentication
│   │   ├── creator.py           # Doctor 1-min quick summary & database admin
│   │   └── intake.py            # Multimodal intake (text, up to 5 docs, audio) & cases
│   ├── __init__.py
│   ├── database.py              # SQLAlchemy engine, session maker & Base declarative
│   ├── gemini_service.py        # 3-key pool & 3-model failover logic, clinical prompts
│   ├── models.py                # DBUser, DBHealthCase, DBIntakeSession ORM tables
│   ├── schemas.py               # Pydantic request & response payload schemas
│   └── security.py              # Password hashing & verification utilities
├── main.py                      # FastAPI app entrypoint, CORS & router registration
├── requirements.txt             # Production dependency specifications
└── README.md
```
