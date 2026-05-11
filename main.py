from fastapi import FastAPI, Depends, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from PyPDF2 import PdfReader
from typing import List
import os
import shutil
import json
import uuid
from datetime import datetime # <--- FIXED IMPORT

from database import engine, get_db
import models, schemas

# =========================
# DATABASE INITIALIZATION
# =========================
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Cloud Print Queue System")

# =========================
# CORS CONFIGURATION
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# DIRECTORY SETUP
# =========================
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/static", StaticFiles(directory="static"), name="static")


# =========================
# HELPER FUNCTIONS
# =========================
def get_page_count(file_path: str) -> int:
    """Extracts page count if file is a PDF."""
    if file_path.lower().endswith(".pdf"):
        try:
            with open(file_path, "rb") as f:
                reader = PdfReader(f)
                return len(reader.pages)
        except Exception as e:
            print(f"PDF Read Error: {e}")
            return 1
    return 1

# =========================
# ROUTES
# =========================

@app.get("/")
def home():
    return FileResponse("static/index.html")

@app.post("/upload")
async def upload_file(
    name: str = Form(...),
    roll: str = Form(...),
    options: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # 1. Parse JSON options
    try:
        settings = json.loads(options)
        if isinstance(settings, str): 
            settings = json.loads(settings)
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON format")

    # 2. Secure & Save File
    file_ext = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    finally:
        await file.close()

    # 3. Process Metadata & Math
    page_count = get_page_count(file_path)
    
    # B&W = 1, Color = 10
    color_val = settings.get("color", "B&W")
    base_rate = 10 if color_val == "Color" else 1
    copies = int(settings.get("copies", 1))
    
    # ACTUAL TOTAL COST MATH
    total_cost = float(base_rate * page_count * copies)

    # 4. Save to Database
    try:
        db_job = models.PrintJob(
            user_name=name,
            roll_number=roll,
            file_url=f"/uploads/{unique_filename}",
            page_count=page_count,
            page_settings=settings,
            status="Queued",
            total_cost=total_cost,
            # datetime.now() uses your Mumbai system time
            timestamp=datetime.now() 
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
    except Exception as e:
        if os.path.exists(file_path): os.remove(file_path)
        print(f"DB Error: {e}")
        # If this fails, it's usually because the .db file doesn't have the 'total_cost' column
        raise HTTPException(status_code=500, detail="Database Save Failed. Delete your .db file and restart.")

    return {
        "status": "success",
        "job_id": db_job.id,
        "total_cost": total_cost,
        "pages": page_count,
        "time": db_job.timestamp.strftime("%I:%M %p")
    }

@app.get("/queue", response_model=List[schemas.PrintJobResponse])
def get_queue(db: Session = Depends(get_db)):
    return db.query(models.PrintJob).filter(models.PrintJob.status == "Queued").all()

@app.patch("/complete/{job_id}")
def complete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(models.PrintJob).filter(models.PrintJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job.status = "Completed"
    db.commit()
    return {"message": "done"}

@app.get("/view/{filename}")
def view_file(filename: str):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404)
    return FileResponse(file_path)