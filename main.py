from fastapi import FastAPI, Depends, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List
import os
import shutil
import json

from database import engine, Base, get_db
import models, schemas

# Create database tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Cloud-Based Print Queue Management System")

# Enable CORS for the frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create local uploads directory if it doesn't exist
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Mount static files (Frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")
# Mount uploads directory to serve files locally if needed
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

def upload_to_cloud(file: UploadFile) -> str:
    """
    Service function to handle file storage.
    Currently saves locally. Can be replaced with Azure Blob Storage logic.
    """
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return f"/uploads/{file.filename}"

    # --- Future Azure Blob Storage implementation ---
    # from azure.storage.blob import BlobServiceClient
    # connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    # blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    # container_client = blob_service_client.get_container_client("print-jobs")
    # blob_client = container_client.get_blob_client(file.filename)
    # blob_client.upload_blob(file.file)
    # return blob_client.url

@app.get("/")
def read_root():
    return FileResponse("static/index.html")

@app.post("/upload", response_model=schemas.PrintJobResponse)
async def upload_file(
    name: str = Form(...),
    roll: str = Form(...),
    options: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        settings_dict = json.loads(options)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid options JSON")

    # 1. Store the file (locally for now)
    file_url = upload_to_cloud(file)

    # 2. Save metadata to SQLite
    db_job = models.PrintJob(
        user_name=name,
        roll_number=roll,
        file_url=file_url,
        page_settings=settings_dict,
        status="Queued"
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    return db_job

@app.get("/queue", response_model=List[schemas.PrintJobResponse])
def get_queue(db: Session = Depends(get_db)):
    """Fetch all print jobs that are currently 'Queued', sorted by oldest first."""
    jobs = db.query(models.PrintJob).filter(models.PrintJob.status == "Queued").order_by(models.PrintJob.timestamp.asc()).all()
    return jobs

@app.patch("/complete/{job_id}", response_model=schemas.PrintJobResponse)
def complete_job(job_id: int, db: Session = Depends(get_db)):
    """Update a specific print job status to 'Completed'."""
    job = db.query(models.PrintJob).filter(models.PrintJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Print job not found")
    
    job.status = "Completed"
    db.commit()
    db.refresh(job)
    return job
