from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi import Request
from sqlalchemy.orm import Session
from typing import List
import os
import json
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions, ContentSettings
import razorpay
from pydantic import BaseModel

from database import engine, get_db
import models, schemas

# =========================
# DATABASE INITIALIZATION
# =========================
# Using lifespan (recommended over deprecated @app.on_event).
# Tables are created automatically on first run against Azure SQL.
# If the tables already exist, create_all() is a no-op — safe to run every time.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create all tables defined in models.py if they don't exist."""
    print("[Startup] Connecting to Azure SQL and initialising schema...")
    models.Base.metadata.create_all(bind=engine)
    print("[Startup] Schema ready.")
    yield  # App runs here
    # (Add any shutdown/cleanup logic below the yield if needed)
    print("[Shutdown] Database connections released.")


app = FastAPI(title="Cloud Print Queue System", lifespan=lifespan)

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
# AZURE STORAGE SETUP
# =========================
load_dotenv()
AZURE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_CONTAINER_NAME = os.getenv("AZURE_CONTAINER_NAME", "print-jobs")

blob_service_client = None
if AZURE_CONNECTION_STRING:
    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
        # Ensure container exists
        container_client = blob_service_client.get_container_client(AZURE_CONTAINER_NAME)
        if not container_client.exists():
            container_client.create_container()
            print(f"[Azure] Created container '{AZURE_CONTAINER_NAME}'")
    except Exception as e:
        print(f"[Azure Init Error] {e}")

# =========================
# PHONEPE SETUP
# =========================
PHONEPE_MERCHANT_ID = os.getenv("PHONEPE_MERCHANT_ID")
PHONEPE_SALT_KEY = os.getenv("PHONEPE_SALT_KEY")
PHONEPE_SALT_INDEX = os.getenv("PHONEPE_SALT_INDEX")
PHONEPE_ENV = os.getenv("PHONEPE_ENV", "UAT")
import base64
import hashlib
import requests
from fastapi.responses import RedirectResponse

# =========================
# HELPER FUNCTIONS
# =========================
def calculate_job_duration_seconds(page_count: int, settings: dict) -> int:
    """Calculates realistic printer time based on settings."""
    duration = 5  # Fixed spooling/warmup time per job
    
    color_val = settings.get("color", "B&W")
    paper_size = settings.get("size", "A4")
    sides = settings.get("sides", "Single")
    copies = int(settings.get("copies", 1))
    
    time_per_page = 2 # Base time for A4 B&W Single
    if color_val == "Color":
        time_per_page += 2
    if paper_size == "A3":
        time_per_page += 3
    if sides == "Double":
        time_per_page += 3
        
    duration += (page_count * copies * time_per_page)
    return duration

# =========================
# ROUTES
# =========================

@app.get("/")
def home():
    return FileResponse("static/index.html")

@app.get("/admin")
def admin_page():
    return FileResponse("static/admin.html")

@app.post("/upload-pending")
async def upload_pending(
    name: str = Form(...),
    roll: str = Form(...),
    options: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    if not PHONEPE_MERCHANT_ID:
        raise HTTPException(status_code=500, detail="PhonePe is not configured")

    # 1. Parse JSON options
    try:
        settings = json.loads(options)
        if isinstance(settings, str): 
            settings = json.loads(settings)
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON format")

    # 2. Upload to Azure Blob Storage (or local)
    file_ext = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    
    file_url = ""
    if blob_service_client:
        try:
            blob_client = blob_service_client.get_blob_client(container=AZURE_CONTAINER_NAME, blob=unique_filename)
            blob_client.upload_blob(
                file.file, 
                content_settings=ContentSettings(content_type=file.content_type)
            )
            
            sas_token = generate_blob_sas(
                account_name=blob_service_client.account_name,
                container_name=AZURE_CONTAINER_NAME,
                blob_name=unique_filename,
                account_key=blob_service_client.credential.account_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.now(timezone.utc) + timedelta(days=30)
            )
            file_url = f"{blob_client.url}?{sas_token}"
        except Exception as e:
            raise HTTPException(status_code=500, detail="Cloud Storage Upload Failed")
    else:
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        try:
            with open(file_path, "wb") as buffer:
                buffer.write(file.file.read())
            file_url = f"/uploads/{unique_filename}"
        except Exception as e:
            raise HTTPException(status_code=500, detail="Local Storage Upload Failed")

    # 3. Process Metadata & Math
    page_count = int(settings.get("pageCount", 1))
    color_val = settings.get("color", "B&W")
    base_rate = 10 if color_val == "Color" else 1
    paper_size = settings.get("size", "A4")
    size_multiplier = 2 if paper_size == "A3" else 1
    copies = int(settings.get("copies", 1))
    
    total_cost = float(base_rate * size_multiplier * page_count * copies)

    transaction_id = f"TXN_{uuid.uuid4().hex[:16]}"
    
    # 4. Save to Database as Pending_Payment
    try:
        db_job = models.PrintJob(
            user_name=name,
            roll_number=roll,
            file_url=file_url,
            page_count=page_count,
            page_settings=settings,
            status="Pending_Payment",
            total_cost=total_cost,
            transaction_id=transaction_id,
            timestamp=datetime.now() 
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Database Save Failed")

    amount_in_paise = int(total_cost * 100)
    
    # Use environment variable for base URL, default to localhost for local testing
    base_url = os.getenv("BASE_URL", "http://localhost:8000")
    
    payload = {
        "merchantId": PHONEPE_MERCHANT_ID,
        "merchantTransactionId": transaction_id,
        "merchantUserId": f"USER_{roll}",
        "amount": amount_in_paise,
        "redirectUrl": f"{base_url}/payment/callback",
        "redirectMode": "POST",
        "paymentInstrument": {
            "type": "PAY_PAGE"
        }
    }
    
    payload_json = json.dumps(payload)
    base64_payload = base64.b64encode(payload_json.encode('utf-8')).decode('utf-8')
    
    endpoint = "/pg/v1/pay"
    string_to_hash = base64_payload + endpoint + PHONEPE_SALT_KEY
    sha256_hash = hashlib.sha256(string_to_hash.encode('utf-8')).hexdigest()
    x_verify = f"{sha256_hash}###{PHONEPE_SALT_INDEX}"
    
    headers = {
        "Content-Type": "application/json",
        "X-VERIFY": x_verify
    }
    
    # Determine Host based on ENV
    host_url = "https://api-preprod.phonepe.com/apis/pg-sandbox" if PHONEPE_ENV == "UAT" else "https://api.phonepe.com/apis/hermes"
    
    try:
        response = requests.post(
            f"{host_url}{endpoint}",
            json={"request": base64_payload},
            headers=headers
        )
        res_data = response.json()
        if res_data.get("success"):
            redirect_url = res_data["data"]["instrumentResponse"]["redirectInfo"]["url"]
            return {"redirect_url": redirect_url}
        else:
            print("PhonePe Error:", res_data)
            raise HTTPException(status_code=500, detail="Failed to initialize PhonePe payment")
    except Exception as e:
        print("PhonePe Request Error:", e)
        raise HTTPException(status_code=500, detail="Payment gateway error")

@app.post("/payment/callback")
async def payment_callback(transactionId: str = Form(...), code: str = Form(...), db: Session = Depends(get_db)):
    """Verifies the payment via PhonePe Status API and redirects back to frontend."""
    txn_id = transactionId
    
    if not txn_id:
        return RedirectResponse(url="/?success=false&reason=Missing_Transaction_ID", status_code=303)
        
    """Verifies the payment via PhonePe Status API and redirects back to frontend."""
    endpoint = f"/pg/v1/status/{PHONEPE_MERCHANT_ID}/{txn_id}"
    
    string_to_hash = endpoint + PHONEPE_SALT_KEY
    sha256_hash = hashlib.sha256(string_to_hash.encode('utf-8')).hexdigest()
    x_verify = f"{sha256_hash}###{PHONEPE_SALT_INDEX}"
    
    headers = {
        "Content-Type": "application/json",
        "X-VERIFY": x_verify,
        "X-MERCHANT-ID": PHONEPE_MERCHANT_ID
    }
    
    host_url = "https://api-preprod.phonepe.com/apis/pg-sandbox" if PHONEPE_ENV == "UAT" else "https://api.phonepe.com/apis/hermes"
    
    try:
        response = requests.get(f"{host_url}{endpoint}", headers=headers)
        res_data = response.json()
        
        db_job = db.query(models.PrintJob).filter(models.PrintJob.transaction_id == txn_id).first()
        
        if res_data.get("success") and res_data["data"]["state"] == "COMPLETED":
            if db_job:
                db_job.status = "Queued"
                db.commit()
            return RedirectResponse(url="/?success=true", status_code=303)
        else:
            if db_job:
                db_job.status = "Payment_Failed"
                db.commit()
            return RedirectResponse(url="/?success=false&reason=Payment_Failed", status_code=303)
            
    except Exception as e:
        print("Status Check Error:", e)
        return RedirectResponse(url="/?success=false&reason=Server_Error", status_code=303)

@app.get("/queue", response_model=List[schemas.PrintJobResponse])
def get_queue(db: Session = Depends(get_db)):
    return db.query(models.PrintJob).filter(models.PrintJob.status == "Queued").all()

@app.get("/queue/status")
def get_queue_count(db: Session = Depends(get_db)):
    jobs = db.query(models.PrintJob).filter(models.PrintJob.status == "Queued").all()
    total_seconds = 0
    for job in jobs:
        total_seconds += calculate_job_duration_seconds(job.page_count, job.page_settings)
    return {"count": len(jobs), "estimated_wait_seconds": total_seconds}

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

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)