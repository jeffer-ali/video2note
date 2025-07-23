from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from video_note_generator import VideoNoteGenerator
from check_illegal_report import CheckIllegalReport

app = FastAPI()
generator = VideoNoteGenerator()
checker = CheckIllegalReport()

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
        result = generator.generate_xhs_note_from_audio(request.url)
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=500, detail=result["error"])
        return {
            "note": result["note"],
            "transcript": result["transcript"],
            "organized_content": result["organized_content"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate_wj_note_from_audio")
def generate_wj_note_from_audio(request: UrlRequest):
    try:
        result = generator.generate_wj_note_from_audio(request.url)
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=500, detail=result["error"])
        return {
            "transcript": result["transcript"],
            "checked_content": result["checked_content"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
@app.post("/check_illegal_from_image")
def generate_report_from_detail(request: UrlRequest):
    try:
        result = checker.generate_report_from_detail(request.url)
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=500, detail=result["error"])
        return {
            "transcript": result["transcript"],
            "checked_content": result["checked_content"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))       