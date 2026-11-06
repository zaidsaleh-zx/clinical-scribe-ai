"""
Clinical Scribe AI - HF Spaces Entry Point

This is the root app.py that HF Spaces Docker will run on port 7860.
It imports the FastAPI application from the backend module.
"""

from backend.main import app

# The app is now ready to be run by Uvicorn:
# uvicorn app:app --host 0.0.0.0 --port 7860

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
