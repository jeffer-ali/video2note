from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from video_note_generator import VideoNoteGenerator

app = FastAPI()
generator = VideoNoteGenerator()

class UrlRequest(BaseModel):
    url: str

@app.get("/")
def read_root():
    return {"msg": "Hello World"}
    
# @app.post("/generate_xhs_note")
# def generate_xhs_note(request: UrlRequest):
#     try:
#         # 假设你有这样一个函数
#         note = video_note_generator.generate_xhs_note_from_url(request.url)
#         return {"note": note}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate_xhs_note_from_audio")
def generate_xhs_note_from_audio(request: UrlRequest):
    try:
        note = generator.generate_xhs_note_from_audio(request.url)
        return {"note": note}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))