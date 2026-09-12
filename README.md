# 🏥 MediKiosk AI — Multilingual Clinical Intake Engine

[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![Google GenAI](https://img.shields.io/badge/Google%20GenAI-Gemini%20Flash%20Cascade-4285F4?style=flat&logo=google)](https://ai.google.dev/)
[![SQLite](https://img.shields.io/badge/Database-SQLite%203-003B57?style=flat&logo=sqlite&logoColor=white)](https://sqlite.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

MediKiosk is an asynchronous clinical intake backend that synthesizes multimodal patient data—text descriptions, chat transcripts, multi-image scans of handwritten prescriptions, lab report PDFs, and audio recordings.

The engine queries all previous sessions in SQLite for a given patient and merges them with new uploads, prompting Google Gemini to maintain a continuous, deduplicated medical profile and a 2-sentence clinical summary translated into any selected language.

---

## ⚡ Features

* **Multimodal Intake**: Accepts conversational dialogues, typed symptoms, multiple prescription photos, lab report PDFs, and speech audio (`.mp3`, `.wav`, `.m4a`).
* **Cumulative Synthesis**: Automatically fetches historical user records from SQLite so every new intake builds upon earlier diagnoses, timelines, and vitals.
* **Multilingual Input & Output**: Seamlessly processes Hindi (Devanagari/Hinglish), English, and regional dialects, translating the final structured output into the user's chosen target language.
* **Dual-Key & Multi-Model Failover**: Automatically cascades between `gemini-2.5-flash`, `gemini-1.5-flash`, and `gemini-1.5-pro` across secondary backup API keys to prevent quota errors and rate limits.
* **Secure Auth**: Implements salted bcrypt password hashing via `pwdlib[bcrypt]` and enforces unique username constraints.

---

## 🏗️ Architecture

```text
Patient Input (Audio / Images / PDFs / Text)
                     │
                     ▼
          FastAPI Gateway Endpoint
                     │
                     ▼
    Fetch User's Prior Sessions from SQLite
                     │
                     ▼
    Combine Past History + Incoming Input
                     │
                     ▼
        Gemini Multimodal Cascade
     (Key Rotation & Model Fallback)
                     │
                     ▼
    Cumulative JSON + 2-Sentence Summary
                     │
                     ▼
    Save to SQLite & Return Response

