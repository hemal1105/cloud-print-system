from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime

class PrintJobCreate(BaseModel):
    user_name: str
    roll_number: str
    page_settings: str # Receives a JSON string from the form

class PrintJobResponse(BaseModel):
    id: int
    user_name: str
    roll_number: str
    file_url: str
    page_settings: Dict[str, Any]
    status: str
    timestamp: datetime

    class Config:
        from_attributes = True
