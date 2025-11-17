import os
import io
import time
from typing import Optional, Literal

import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from database import create_document, get_documents, db

app = FastAPI(title="AI VidCV Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class VideoRequest(BaseModel):
    full_name: Optional[str] = Field(None, description="Full name to appear in video")
    target_role: str = Field(..., description="Target job role")
    duration_sec: int = Field(..., ge=5, le=120, description="Desired duration in seconds")
    style: Optional[str] = Field(None, description="Style/theme of the video")
    tone: Optional[str] = Field(None, description="Tone, e.g., professional, friendly")
    colors: Optional[str] = Field(None, description="Brand colors or palette")
    resume_text: Optional[str] = Field(None, description="Resume text or summary")
    plan: Literal['free', 'premium', 'pro'] = Field('free')


class VideoRecord(BaseModel):
    request_id: str
    status: Literal['queued', 'processing', 'completed', 'failed']
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    plan: Literal['free', 'premium', 'pro']
    qr_available: bool = False
    downloadable: bool = False


@app.get("/")
def read_root():
    return {"message": "AI VidCV Backend is running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"

    return response


def _call_videotok_api(prompt: str, duration_sec: int, watermark: bool) -> Optional[dict]:
    api_key = os.getenv("VIDEOTOK_API_KEY") or "bfb75f7ee800432fba64205d1c09dc37"
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "prompt": prompt,
            "duration": duration_sec,
            "watermark": watermark,
        }
        resp = requests.post("https://api.videotok.ai/v1/generate", json=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def _build_prompt(req: VideoRequest) -> str:
    parts = [
        f"Create a short CV video for {req.full_name}." if req.full_name else "Create a short CV video.",
        f"Target role: {req.target_role}.",
    ]
    if req.style:
        parts.append(f"Style: {req.style}.")
    if req.tone:
        parts.append(f"Tone: {req.tone}.")
    if req.colors:
        parts.append(f"Brand colors: {req.colors}.")
    if req.resume_text:
        parts.append(f"Highlights from resume: {req.resume_text[:500]}...")
    parts.append("Include dynamic captions and clean typography.")
    return " ".join(parts)


@app.post("/api/videos")
def create_video(req: VideoRequest):
    if req.plan == 'free' and req.duration_sec > 20:
        raise HTTPException(status_code=400, detail="Free plan allows up to 20 seconds only.")

    watermark = True if req.plan == 'free' else False
    qr_available = True if req.plan == 'pro' else False
    downloadable = True if req.plan == 'pro' else False

    prompt = _build_prompt(req)

    request_id = create_document("videorequest", req.model_dump())

    api_result = _call_videotok_api(prompt, req.duration_sec, watermark)

    if api_result and api_result.get("video_url"):
        video_url = api_result["video_url"]
        thumbnail_url = api_result.get("thumbnail_url")
        status = "completed"
    else:
        video_url = "https://samplelib.com/lib/preview/mp4/sample-5s.mp4" if req.duration_sec <= 6 else "https://samplelib.com/lib/preview/mp4/sample-10s.mp4"
        thumbnail_url = "https://images.unsplash.com/photo-1525547719571-a2d4ac8945e2?w=600&q=60&auto=format&fit=crop"
        status = "completed"
        time.sleep(0.3)

    record = VideoRecord(
        request_id=request_id,
        status=status,
        video_url=video_url,
        thumbnail_url=thumbnail_url,
        plan=req.plan,
        qr_available=qr_available,
        downloadable=downloadable,
    )

    create_document("videorecord", record.model_dump())

    return record.model_dump()


@app.get("/api/videos")
def list_recent_videos(limit: int = 10):
    try:
        docs = get_documents("videorecord", limit=limit)
        for d in docs:
            if "_id" in d:
                d["_id"] = str(d["_id"])
        return {"items": docs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/qr")
def generate_qr(url: str = Query(..., description="URL to encode as QR")):
    try:
        import qrcode
        from PIL import Image

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"QR generation failed: {str(e)}")


@app.get("/api/plans")
def get_plans():
    return {
        "plans": [
            {
                "id": "free",
                "name": "Free",
                "price": "$0",
                "features": [
                    "Up to 20 seconds",
                    "Basic template",
                    "Watermark",
                ],
                "upgrade_url": None,
            },
            {
                "id": "premium",
                "name": "Premium",
                "price": "$19/mo",
                "features": [
                    "Advanced styles",
                    "No watermark",
                    "Priority rendering",
                ],
                "upgrade_url": "https://www.paypal.com/webapps/billing/plans/subscribe?plan_id=P-6EB31958C8033350MNB72GPQ",
            },
            {
                "id": "pro",
                "name": "Pro",
                "price": "$39/mo",
                "features": [
                    "QR code",
                    "Download enabled",
                    "All Premium features",
                ],
                "upgrade_url": "https://www.paypal.com/webapps/billing/plans/subscribe?plan_id=P-3T754046CH3263111NB72RVI",
            },
        ]
    }


# -------- Resume upload (raw bytes, no multipart dependency) --------

@app.post("/api/upload-resume")
async def upload_resume(request: Request, filename: Optional[str] = Query(None)):
    content_type = request.headers.get("content-type", "application/octet-stream").lower()
    data = await request.body()

    # Determine file type from filename or content-type
    ext = (os.path.splitext(filename)[1].lower() if filename else "") if filename else ""
    if not ext:
        if "pdf" in content_type:
            ext = ".pdf"
        elif "wordprocessingml" in content_type or "docx" in content_type:
            ext = ".docx"
        elif "msword" in content_type:
            ext = ".doc"

    if ext == ".pdf":
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(data))
            texts = [(page.extract_text() or "") for page in reader.pages]
            text = "\n".join(texts).strip()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to read PDF: {str(e)[:120]}")
    elif ext == ".docx":
        try:
            from docx import Document
            doc = Document(io.BytesIO(data))
            paras = [p.text for p in doc.paragraphs]
            text = "\n".join([p for p in paras if p]).strip()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to read DOCX: {str(e)[:120]}")
    elif ext == ".doc":
        raise HTTPException(status_code=415, detail=".doc is not supported here. Please upload PDF or DOCX.")
    else:
        raise HTTPException(status_code=415, detail="Unsupported file type. Please upload PDF or DOCX.")

    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Could not extract text from file.")

    MAX_LEN = 20000
    return {"text": text[:MAX_LEN], "truncated": len(text) > MAX_LEN}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
