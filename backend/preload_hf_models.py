# backend/preload_hf_models.py
import os
import platform

# Set default HF_HOME to ProgramData shared folder if not already set
if platform.system() == "Windows" and not os.environ.get("HF_HOME"):
    program_data = os.environ.get("ProgramData") or os.environ.get("ALLUSERSPROFILE") or "C:\\ProgramData"
    os.environ["HF_HOME"] = os.path.abspath(os.path.join(program_data, "Quantime", "hf_cache"))

print(f"Preloading Hugging Face models using cache folder: {os.environ.get('HF_HOME')}", flush=True)

# 1. Preload Kokoro
try:
    from kokoro import KPipeline
    print("Loading Kokoro-82M model...", flush=True)
    pipeline = KPipeline(lang_code='a')
    print("Kokoro-82M model cached successfully.", flush=True)
except Exception as e:
    print(f"Error caching Kokoro-82M: {e}", flush=True)

# 2. Preload Sentence-Transformers (for ChromaDB)
try:
    from huggingface_hub import snapshot_download
    print("Loading sentence-transformers/all-MiniLM-L6-v2...", flush=True)
    snapshot_download(repo_id="sentence-transformers/all-MiniLM-L6-v2")
    print("sentence-transformers cached successfully.", flush=True)
except Exception as e:
    print(f"Error caching sentence-transformers: {e}", flush=True)
