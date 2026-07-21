import os
import json
import time
import requests

OLLAMADB_URL = "https://ollamadb.dev/api/v1/models"
CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache", "ollama_catalog.json")
CACHE_MAX_AGE_HOURS = 24

FALLBACK_MODELS = [
    # Light models
    {"model_identifier": "llama3.2:1b", "description": "Meta's extremely capable lightweight reasoning model", "labels": ["1B"], "pulls": 1500000, "capability": "reasoning fast"},
    {"model_identifier": "llama3.2:3b", "description": "Excellent balance of speed and instruction following", "labels": ["3B"], "pulls": 1400000, "capability": "reasoning"},
    {"model_identifier": "qwen2.5:0.5b", "description": "Ultra-fast, perfect for extremely low RAM setups", "labels": ["0.5B"], "pulls": 500000, "capability": "fast"},
    {"model_identifier": "qwen2.5:1.5b", "description": "Great coding and logic in a tiny footprint", "labels": ["1.5B"], "pulls": 800000, "capability": "coding"},
    {"model_identifier": "qwen2.5:3b", "description": "Strong multi-lingual and logic performance", "labels": ["3B"], "pulls": 700000, "capability": "general reasoning"},
    {"model_identifier": "phi3:mini", "description": "Microsoft's highly capable compact model", "labels": ["3.8B"], "pulls": 1200000, "capability": "reasoning"},
    {"model_identifier": "gemma2:2b", "description": "Google's 2B model, punches far above its weight", "labels": ["2B"], "pulls": 900000, "capability": "general"},
    {"model_identifier": "starcoder2:3b", "description": "Great lightweight autocomplete model", "labels": ["3B"], "pulls": 500000, "capability": "coding"},
    {"model_identifier": "tinyllama", "description": "Classic 1B model, very fast inference", "labels": ["1B"], "pulls": 1100000, "capability": "fast"},
    {"model_identifier": "deepseek-coder:1.3b", "description": "Best-in-class coding model for under 1GB", "labels": ["1.3B"], "pulls": 600000, "capability": "coding"},
    {"model_identifier": "smollm:1.7b", "description": "Highly efficient reasoning model", "labels": ["1.7B"], "pulls": 400000, "capability": "reasoning"},
    {"model_identifier": "orca-mini", "description": "Highly tuned for explanation and reasoning", "labels": ["3B"], "pulls": 500000, "capability": "reasoning"},
    # Standard models
    {"model_identifier": "llama3.1:8b", "description": "Meta's flagship 8B model. Superb logic and RAG.", "labels": ["8B"], "pulls": 5000000, "capability": "general reasoning"},
    {"model_identifier": "qwen2.5:7b", "description": "Top-tier coding, multi-lingual, and general reasoning", "labels": ["7B"], "pulls": 2000000, "capability": "coding tool-use"},
    {"model_identifier": "mistral:7b", "description": "Excellent standard 7B model, classic reliable choice", "labels": ["7B"], "pulls": 8000000, "capability": "general"},
    {"model_identifier": "phi3:medium", "description": "Advanced reasoning capabilities from Microsoft", "labels": ["14B"], "pulls": 900000, "capability": "reasoning"},
    {"model_identifier": "gemma2:9b", "description": "High-quality model from Google, very creative", "labels": ["9B"], "pulls": 1500000, "capability": "creative"},
    {"model_identifier": "codellama:7b", "description": "Meta's fine-tuned model specifically for programming", "labels": ["7B"], "pulls": 2000000, "capability": "coding"},
    {"model_identifier": "deepseek-coder:6.7b", "description": "Incredibly capable standard-tier coding model", "labels": ["6.7B"], "pulls": 1200000, "capability": "coding"},
    {"model_identifier": "dolphin-mistral:7b", "description": "Uncensored, highly compliant assistant", "labels": ["7B"], "pulls": 900000, "capability": "general uncensored"},
    # Heavy models
    {"model_identifier": "llama3.1:70b", "description": "Meta's most powerful open weights model, exceptional logic", "labels": ["70B"], "pulls": 3000000, "capability": "reasoning tool-use"},
    {"model_identifier": "mixtral:8x7b", "description": "Mistral's MoE model, very fast for its size", "labels": ["47B"], "pulls": 4000000, "capability": "general"},
    {"model_identifier": "command-r", "description": "Cohere's RAG-optimized model, very reliable", "labels": ["35B"], "pulls": 1000000, "capability": "rag tool-use"},
    {"model_identifier": "deepseek-coder-v2", "description": "World-class coding model", "labels": ["16B"], "pulls": 1500000, "capability": "coding"},
    {"model_identifier": "qwen2.5:14b", "description": "Exceptional coding & reasoning, fits perfectly in 16GB", "labels": ["14B"], "pulls": 1100000, "capability": "balanced"},
    {"model_identifier": "mistral-nemo:12b", "description": "Large context window, great intelligence, fast", "labels": ["12B"], "pulls": 1200000, "capability": "general"}
]

FALLBACK_CLOUD_MODELS = [
    {"model_identifier": "gpt-oss:20b-cloud", "description": "Highly capable general purpose cloud model", "labels": ["20B", "Cloud"], "pulls": 5000000, "capability": "general"},
    {"model_identifier": "deepseek-v3.1:671b-cloud", "description": "Massive reasoning model hosted in the cloud", "labels": ["671B", "Cloud"], "pulls": 2000000, "capability": "reasoning"},
    {"model_identifier": "qwen3-coder:480b-cloud", "description": "Unmatched coding capability without local RAM limits", "labels": ["480B", "Cloud"], "pulls": 1500000, "capability": "coding"},
    {"model_identifier": "kimi-k2:1t-cloud", "description": "Trillion parameter model for deep long-context tasks", "labels": ["1T", "Cloud"], "pulls": 1000000, "capability": "long context"},
    {"model_identifier": "gemma4:cloud", "description": "Google's latest cloud-optimized model", "labels": ["Cloud"], "pulls": 800000, "capability": "general"},
    {"model_identifier": "qwen3.5:cloud", "description": "Fast and responsive general assistant", "labels": ["Cloud"], "pulls": 900000, "capability": "assistant"},
    {"model_identifier": "glm-5.1:cloud", "description": "Powerful multi-lingual capabilities", "labels": ["Cloud"], "pulls": 500000, "capability": "multi-lingual"},
    {"model_identifier": "minimax-m2.7:cloud", "description": "Specialized reasoning and logic model", "labels": ["Cloud"], "pulls": 400000, "capability": "logic"},
    {"model_identifier": "nemotron-3-super:cloud", "description": "High-fidelity generation model from NVIDIA", "labels": ["Cloud"], "pulls": 600000, "capability": "creative"},
    {"model_identifier": "kimi-k2.7-code:cloud", "description": "Advanced coding specialist from Kimi", "labels": ["Cloud"], "pulls": 700000, "capability": "coding"}
]

def fetch_ollama_catalog(limit=250):
    """Fetch the current Ollama model library from the community-maintained
    ollamadb.dev mirror. Raises requests.RequestException on failure."""
    resp = requests.get(OLLAMADB_URL, params={"limit": limit}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("models", [])

def _cache_is_fresh():
    if not os.path.exists(CACHE_PATH):
        return False
    age_seconds = time.time() - os.path.getmtime(CACHE_PATH)
    return age_seconds < (CACHE_MAX_AGE_HOURS * 3600)

def _load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def _save_cache(catalog):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(catalog, f)

def get_catalog():
    if _cache_is_fresh():
        cached = _load_cache()
        if cached is not None:
            return cached
    try:
        catalog = fetch_ollama_catalog()
        _save_cache(catalog)
        return catalog
    except requests.RequestException:
        cached = _load_cache()
        return cached if cached is not None else FALLBACK_MODELS

def derive_tags(model):
    tags = []
    pulls = model.get("pulls", 0)
    desc = (model.get("description", "") or "").lower()
    name = (model.get("model_identifier", "") or "").lower()
    
    cap_raw = model.get("capability", "")
    if isinstance(cap_raw, list):
        cap = " ".join(str(c).lower() for c in cap_raw)
    else:
        cap = str(cap_raw).lower()
        
    labels_raw = model.get("labels", [])
    if isinstance(labels_raw, str):
        labels_raw = [labels_raw]
    labels_str = " ".join(str(L).lower() for L in labels_raw)
    
    if pulls > 1_000_000:
        tags.append("Popular")
        
    is_fast = False
    for label in labels_raw:
        label_str = str(label).upper()
        if label_str.endswith("B"):
            try:
                num = float(label_str[:-1])
                if num < 2.5:
                    is_fast = True
            except ValueError:
                pass
    if is_fast:
        tags.append("Fast")
        
    if "reasoning" in cap or "thinking" in cap or "reasoning" in desc or "thinking" in desc:
        tags.append("Reasoning")
        
    if "tool" in cap or "function" in cap or "function-calling" in desc:
        tags.append("Tool-use")
        
    if "vision" in cap or "multimodal" in cap or "vision" in labels_str or "vision" in desc:
        tags.append("Vision")
        
    if "code" in name or "coder" in name or "code" in desc or "coding" in desc:
        tags.append("Coding")
        
    if not tags:
        tags.append("General")
        
    return tags

def estimate_size_gb(model):
    labels = model.get("labels", [])
    if isinstance(labels, str):
        labels = [labels]
        
    for label in labels:
        label_str = str(label).upper()
        if label_str.endswith("B"):
            try:
                params = float(label_str[:-1])
                return params * 0.65
            except ValueError:
                pass
                
    name = str(model.get("model_identifier", "")).lower()
    if "70b" in name: return 70 * 0.65
    if "8x7b" in name: return 47 * 0.65
    if "32b" in name: return 32 * 0.65
    if "14b" in name: return 14 * 0.65
    if "7b" in name or "8b" in name: return 8 * 0.65
    
    return 4.0

def build_recommendations(ram_gb, catalog):
    usable_ram_gb = ram_gb * 0.6
    candidates = []
    
    for m in catalog:
        size_gb = estimate_size_gb(m)
        if size_gb is not None and size_gb <= usable_ram_gb:
            candidates.append({
                "name": m.get("model_identifier", "unknown"),
                "size": f"{size_gb:.1f} GB",
                "desc": m.get("description", ""),
                "tags": derive_tags(m),
                "pulls": m.get("pulls", 0)
            })
            
    candidates.sort(key=lambda m: m.get("pulls", 0), reverse=True)
    return candidates[:12]
