from sqlalchemy import Column, Integer, String, DateTime, JSON
from datetime import datetime
from database import Base

class PrintJob(Base):
    __tablename__ = "print_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String, index=True)
    roll_number = Column(String, index=True)
    file_url = Column(String)  # This will store the local path or Azure Blob URL
    page_settings = Column(JSON)  # e.g., {"color": "B&W", "copies": 1, "duplex": true}
    status = Column(String, default="Queued", index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
