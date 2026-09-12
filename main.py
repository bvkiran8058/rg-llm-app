from fastapi import FastAPI

app = FastAPI(title="LLM App", version="0.1.0")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "LLM App is running"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}