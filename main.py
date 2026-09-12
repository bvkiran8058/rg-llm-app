import os
import time
import logging
from fastapi import FastAPI, Request, HTTPException, Depends, Security
from fastapi.security import APIKeyHeader
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from prometheus_fastapi_instrumentator import Instrumentator
import httpx
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# ---------- Logging (structured, production-style) ----------
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("llm-app")

# ---------- Config ----------
KEY_VAULT_URL = os.getenv("KEY_VAULT_URL")  # e.g. https://kv-llmapp-kiran.vault.azure.net/
HF_SECRET_NAME = os.getenv("HF_SECRET_NAME", "HuggingFaceApiKey")
HF_MODEL_URL = os.getenv("HF_MODEL_URL", "https://api-inference.huggingface.co/models/gpt2")

# Simple in-memory API key store for now — will move customers to a DB later
VALID_API_KEYS = {
    "customer1-key-demo": "customer1",
    "customer2-key-demo": "customer2",
}

# ---------- Fetch HF token from Key Vault at startup ----------
def get_hf_token() -> str:
    if not KEY_VAULT_URL:
        # local/dev fallback only
        return os.getenv("HF_API_KEY", "")
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    secret = client.get_secret(HF_SECRET_NAME)
    return secret.value

HF_TOKEN = get_hf_token()

# ---------- App + rate limiter ----------
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="LLM App", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------- Prometheus metrics at /metrics ----------
Instrumentator().instrument(app).expose(app)

# ---------- Auth ----------
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key not in VALID_API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return VALID_API_KEYS[api_key]

# ---------- Routes ----------
@app.get("/")
def read_root():
    return {"status": "ok", "message": "LLM App is running"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/generate")
@limiter.limit("10/minute")  # per-IP rate limit; per-customer limiting comes with Redis later
async def generate_text(request: Request, prompt: str, customer: str = Depends(verify_api_key)):
    start = time.time()
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(HF_MODEL_URL, headers=headers, json={"inputs": prompt})
    duration = time.time() - start

    if response.status_code != 200:
        logger.error(f"customer={customer} HF_error status={response.status_code}")
        raise HTTPException(status_code=502, detail="Upstream model error")

    result = response.json()
    logger.info(f"customer={customer} prompt_len={len(prompt)} duration={duration:.2f}s")
    return {"customer": customer, "result": result, "duration_seconds": round(duration, 2)}