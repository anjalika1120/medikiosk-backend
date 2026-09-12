@import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import Base, engine
from app.routers import auth, intake

# Initialize database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="MediKiosk Universal Multilingual AI Backend",
    description="Multimodal clinical intake accepting Text, Audio, Scans (OCR), and PDFs with historical aggregation and language translation.",
    version="3.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(intake.router)

@app.get("/")
def health_check():
    return {"status": "active", "service": "MediKiosk Multilingual AI API v3.0"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True) 
