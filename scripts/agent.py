#!/usr/bin/env python3
"""
FreeModel Auto-Coder Agent
ÐÐ½Ð°Ð»Ð¾Ð³ Google Jules. ÐÐ¾Ð´Ð´ÐµÑÐ¶Ð¸Ð²Ð°ÐµÑ Claude (Anthropic API) Ð¸ OpenAI-compatible endpoints.
Ð Ð°Ð±Ð¾ÑÐ°ÐµÑ Ð² GitHub Actions.
"""

import os
import re
import json
import time
import base64
from datetime import datetime
from pathlib import Path

import requests

# ==================== ÐÐÐ¡Ð¢Ð ÐÐÐÐ ====================
AGENT_MODE = os.environ.get("AGENT_MODE", "auto_todo")
API_KEY = os.environ.get("FREEMODEL_API_KEY", "")
GH_PAT = os.environ.get("GH_PAT", "")  # Personal Access Token with full repo: scope
GITHUB_TOKEN = GH_PAT or os.environ.get("GITHUB_TOKEN", "")  # Prefer PAT for PR creation
REPO_FULL_NAME = os.environ.get("REPO_FULL_NAME", "")
MAX_FILES_TO_SCAN = 15
MAX_FILE_SIZE = 50000
MAX_TOKENS = 4000
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
BACKOFF_FACTOR = 1.5
RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 504}
SUPPORTED_API_TYPES = {"anthropic", "openai"}

# ÐÑÐ±Ð¾Ñ API: "anthropic" Ð´Ð»Ñ Claude (cc.freemodel.dev) Ð¸Ð»Ð¸ "openai" Ð´Ð»Ñ GPT (api.freemodel.dev)
API_TYPE = os.environ.get("API_TYPE", "openai").strip().lower()
if API_TYPE not in SUPPORTED_API_TYPES:
    raise ValueError(f"ÐÐµÐ¿Ð¾Ð´Ð´ÐµÑÐ¶Ð¸Ð²Ð°ÐµÐ¼ÑÐ¹ API_TYPE: {API_TYPE}")

if API_TYPE == "anthropic":
    # Claude ÑÐµÑÐµÐ· FreeModel
    BASE_URL = "https://cc.freemodel.dev"
    MODEL = os.environ.get("MODEL", "claude-opus-4-20250514")
    API_URL = f"{BASE_URL}/v1/messages"
else:
    # OpenAI-compatible ÑÐµÑÐµÐ· FreeModel
    BASE_URL = "https://api.freemodel.dev/v1"
    MODEL = os.environ.get("MODEL", "gpt-5.4")
    API_URL = f"{BASE_URL}/chat/completions"

# ==================== ÐÐ ÐÐÐÐ¢Ð« ====================
SYSTEM_PROMPT = """Ð¢Ñ â senior software engineer Ð¸ AI-Ð°ÑÑÐ¸ÑÑÐµÐ½Ñ Ð´Ð»Ñ Ð°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸Ð·Ð°ÑÐ¸Ð¸ ÑÐ°Ð·ÑÐ°Ð±Ð¾ÑÐºÐ¸.
Ð¢Ð²Ð¾Ñ Ð·Ð°Ð´Ð°ÑÐ° â Ð°Ð½Ð°Ð»Ð¸Ð·Ð¸ÑÐ¾Ð²Ð°ÑÑ ÐºÐ¾Ð´Ð¾Ð²ÑÑ Ð±Ð°Ð·Ñ Ð¸ Ð¿ÑÐµÐ´Ð»Ð°Ð³Ð°ÑÑ ÐºÐ¾Ð½ÐºÑÐµÑÐ½ÑÐµ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ñ.

ÐÑÐ°Ð²Ð¸Ð»Ð°:
1. ÐÑÐ²ÐµÑÐ°Ð¹ Ð¢ÐÐÐ¬ÐÐ Ð² ÑÐ¾ÑÐ¼Ð°ÑÐµ JSON Ñ Ð¿Ð¾Ð»ÑÐ¼Ð¸: "analysis" (Ð°Ð½Ð°Ð»Ð¸Ð·), "changes" (ÑÐ¿Ð¸ÑÐ¾Ðº Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ð¹)
2. ÐÐ°Ð¶Ð´Ð¾Ðµ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ðµ Ð´Ð¾Ð»Ð¶Ð½Ð¾ ÑÐ¾Ð´ÐµÑÐ¶Ð°ÑÑ: "file_path", "action" (create|modify|delete), "content" (Ð¿Ð¾Ð»Ð½Ð¾Ðµ ÑÐ¾Ð´ÐµÑÐ¶Ð¸Ð¼Ð¾Ðµ ÑÐ°Ð¹Ð»Ð°)
3. ÐÑÐ»Ð¸ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ð¹ Ð½Ðµ ÑÑÐµÐ±ÑÐµÑÑÑ â Ð²ÐµÑÐ½Ð¸ Ð¿ÑÑÑÐ¾Ð¹ changes []
4. ÐÐµ Ð¸ÑÐ¿Ð¾Ð»ÑÐ·ÑÐ¹ markdown Ð²Ð½ÑÑÑÐ¸ JSON
5. ÐÐ¾Ð´ Ð´Ð¾Ð»Ð¶ÐµÐ½ Ð±ÑÑÑ ÑÐ°Ð±Ð¾ÑÐ¸Ð¼, Ð±ÐµÐ· Ð¿Ð»ÐµÐ¹ÑÑÐ¾Ð»Ð´ÐµÑÐ¾Ð²
6. ÐÐ¾Ð¼Ð¼ÐµÐ½ÑÐ°ÑÐ¸Ð¸ Ð¿Ð¸ÑÐ¸ Ð½Ð° ÑÑÑÑÐºÐ¾Ð¼ ÑÐ·ÑÐºÐµ
7. Ð¡Ð»ÐµÐ´ÑÐ¹ Ð»ÑÑÑÐ¸Ð¼ Ð¿ÑÐ°ÐºÑÐ¸ÐºÐ°Ð¼: ÑÐ¸ÑÑÑÐ¹ ÐºÐ¾Ð´, DRY, SOLID
"""

MODE_PROMPTS = {
    "auto_todo": """ÐÑÐ¾Ð°Ð½Ð°Ð»Ð¸Ð·Ð¸ÑÑÐ¹ ÐºÐ¾Ð´Ð¾Ð²ÑÑ Ð±Ð°Ð·Ñ. ÐÐ°Ð¹Ð´Ð¸ TODO, FIXME, XXX ÐºÐ¾Ð¼Ð¼ÐµÐ½ÑÐ°ÑÐ¸Ð¸ Ð¸ ÑÐµÐ°Ð»Ð¸Ð·ÑÐ¹ Ð¸Ñ.
ÐÑÐ»Ð¸ Ð½Ð°Ð¹Ð´ÐµÑÑ Ð½ÐµÐ·Ð°Ð²ÐµÑÑÑÐ½Ð½ÑÑ ÑÑÐ½ÐºÑÐ¸Ñ (pass, NotImplementedError) â ÑÐµÐ°Ð»Ð¸Ð·ÑÐ¹ ÐµÑ.
ÐÐµÑÐ½Ð¸ JSON Ñ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸ÑÐ¼Ð¸.""",
    
    "refactor": """ÐÑÐ¾Ð°Ð½Ð°Ð»Ð¸Ð·Ð¸ÑÑÐ¹ ÐºÐ¾Ð´Ð¾Ð²ÑÑ Ð±Ð°Ð·Ñ. ÐÐ°Ð¹Ð´Ð¸:
- ÐÑÐ±Ð»Ð¸ÑÐ¾Ð²Ð°Ð½Ð¸Ðµ ÐºÐ¾Ð´Ð°
- Ð¡Ð»Ð¸ÑÐºÐ¾Ð¼ Ð´Ð»Ð¸Ð½Ð½ÑÐµ ÑÑÐ½ÐºÑÐ¸Ð¸ (>50 ÑÑÑÐ¾Ðº)
- ÐÐ°Ð³Ð¸ÑÐµÑÐºÐ¸Ðµ ÑÐ¸ÑÐ»Ð°
- ÐÐµÐ¸ÑÐ¿Ð¾Ð»ÑÐ·ÑÐµÐ¼ÑÐµ Ð¸Ð¼Ð¿Ð¾ÑÑÑ/Ð¿ÐµÑÐµÐ¼ÐµÐ½Ð½ÑÐµ
- ÐÐ°ÑÑÑÐµÐ½Ð¸Ñ DRY/SOLID

ÐÐµÑÐ½Ð¸ JSON Ñ ÑÐµÑÐ°ÐºÑÐ¾ÑÐ¸Ð½Ð³Ð¾Ð¼. ÐÐµ Ð¼ÐµÐ½ÑÐ¹ Ð»Ð¾Ð³Ð¸ÐºÑ ÑÐ°Ð±Ð¾ÑÑ â ÑÐ¾Ð»ÑÐºÐ¾ ÑÐ»ÑÑÑÐ¸ ÐºÐ¾Ð´.""",
    
    "bugfix": """ÐÑÐ¾Ð°Ð½Ð°Ð»Ð¸Ð·Ð¸ÑÑÐ¹ ÐºÐ¾Ð´Ð¾Ð²ÑÑ Ð±Ð°Ð·Ñ Ð½Ð° Ð½Ð°Ð»Ð¸ÑÐ¸Ðµ Ð¿Ð¾ÑÐµÐ½ÑÐ¸Ð°Ð»ÑÐ½ÑÑ Ð±Ð°Ð³Ð¾Ð²:
- ÐÐµÐ¾Ð±ÑÐ°Ð±Ð¾ÑÐ°Ð½Ð½ÑÐµ edge cases
- Ð£ÑÐµÑÐºÐ¸ ÑÐµÑÑÑÑÐ¾Ð²
- Race conditions
- SQL injection / XSS ÑÑÐ·Ð²Ð¸Ð¼Ð¾ÑÑÐ¸
- ÐÐµÐ¿ÑÐ°Ð²Ð¸Ð»ÑÐ½Ð°Ñ ÑÐ°Ð±Ð¾ÑÐ° Ñ None/null

ÐÐµÑÐ½Ð¸ JSON Ñ Ð¸ÑÐ¿ÑÐ°Ð²Ð»ÐµÐ½Ð¸ÑÐ¼Ð¸.""",
    
    "review": """ÐÑÐ¾Ð²ÐµÐ´Ð¸ code review Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ñ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ð¹. Ð£ÐºÐ°Ð¶Ð¸:
- Ð§ÑÐ¾ ÑÐ´ÐµÐ»Ð°Ð½Ð¾ ÑÐ¾ÑÐ¾ÑÐ¾
- Ð§ÑÐ¾ Ð¼Ð¾Ð¶Ð½Ð¾ ÑÐ»ÑÑÑÐ¸ÑÑ
- ÐÑÐ¸ÑÐ¸ÑÐµÑÐºÐ¸Ðµ Ð·Ð°Ð¼ÐµÑÐ°Ð½Ð¸Ñ

ÐÐµÑÐ½Ð¸ JSON Ñ Ð¿ÑÐµÐ´Ð»Ð°Ð³Ð°ÐµÐ¼ÑÐ¼Ð¸ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸ÑÐ¼Ð¸ (ÐµÑÐ»Ð¸ ÐµÑÑÑ)."""
}

# ==================== GITHUB API ====================
GITHUB_API = "https://api.github.com"
HEADERS_GH = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}


def log(msg):
    print(f"[AGENT] {msg}")


class HttpClient:
    """HTTP-ÐºÐ»Ð¸ÐµÐ½Ñ Ñ Ð¿Ð¾Ð²ÑÐ¾ÑÐ½ÑÐ¼Ð¸ Ð¿Ð¾Ð¿ÑÑÐºÐ°Ð¼Ð¸ Ð¸ ÑÐºÑÐ¿Ð¾Ð½ÐµÐ½ÑÐ¸Ð°Ð»ÑÐ½Ð¾Ð¹ Ð·Ð°Ð´ÐµÑÐ¶ÐºÐ¾Ð¹."""

    def __init__(self, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES, backoff_factor=BACKOFF_FACTOR):
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.session = requests.Session()

    def request(self, method, url, headers=None, **kwargs):
        timeout = kwargs.pop("timeout", self.timeout)
        last_exception = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(method, url, headers=headers, timeout=timeout, **kwargs)
                if response.status_code in RETRY_STATUS_CODES and attempt < self.max_retries:
                    self._sleep_before_retry(attempt, method, url, response.status_code)
                    continue
                return response
            except requests.RequestException as error:
                last_exception = error
                if attempt >= self.max_retries:
                    raise
                self._sleep_before_retry(attempt, method, url, str(error))

        if last_exception is not None:
            raise last_exception
        raise RuntimeError("ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ Ð²ÑÐ¿Ð¾Ð»Ð½Ð¸ÑÑ HTTP-Ð·Ð°Ð¿ÑÐ¾Ñ")

    def _sleep_before_retry(self, attempt, method, url, reason):
        delay = self.backoff_factor ** (attempt - 1)
        log(f"ÐÐ¾Ð²ÑÐ¾Ñ HTTP-Ð·Ð°Ð¿ÑÐ¾ÑÐ° {method} {url} ÑÐµÑÐµÐ· {delay:.1f}Ñ. ÐÑÐ¸ÑÐ¸Ð½Ð°: {reason}")
        time.sleep(delay)


class AIProvider:
    """ÐÐ°Ð·Ð¾Ð²ÑÐ¹ Ð¸Ð½ÑÐµÑÑÐµÐ¹Ñ AI-Ð¿ÑÐ¾Ð²Ð°Ð¹Ð´ÐµÑÐ°."""

    def __init__(self, api_key, model, api_url, http_client):
        self.api_key = api_key
        self.model = model
        self.api_url = api_url
        self.http_client = http_client

    def generate(self, prompt):
        raise NotImplementedError


class AnthropicProvider(AIProvider):
    """ÐÑÐ¾Ð²Ð°Ð¹Ð´ÐµÑ Anthropic API ÑÐµÑÐµÐ· FreeModel."""

    def generate(self, prompt):
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}]
        }
        log(f"ÐÑÐ¿ÑÐ°Ð²ÐºÐ° Ð·Ð°Ð¿ÑÐ¾ÑÐ° Ð² Claude ÑÐµÑÐµÐ· FreeModel ({self.model})...")
        response = self.http_client.request("POST", self.api_url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        content = data["content"][0]["text"]
        if "access denied" in content.lower() or "restricted" in content.lower():
            log("ÐÐ¨ÐÐÐÐ: FreeModel Claude endpoint ÑÑÐµÐ±ÑÐµÑ Ð¾ÑÐ¸ÑÐ¸Ð°Ð»ÑÐ½ÑÐ¹ Claude Code CLI.")
            log(f"Ð¢ÐµÐ»Ð¾ Ð¾ÑÐ²ÐµÑÐ°: {content[:200]}")
            raise RuntimeError(f"API Ð·Ð°Ð±Ð»Ð¾ÐºÐ¸ÑÐ¾Ð²Ð°Ð½: {content[:200]}")
        return content


class OpenAICompatibleProvider(AIProvider):
    """ÐÑÐ¾Ð²Ð°Ð¹Ð´ÐµÑ OpenAI-compatible API ÑÐµÑÐµÐ· FreeModel."""

    def generate(self, prompt):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ]
        }
        log(f"ÐÑÐ¿ÑÐ°Ð²ÐºÐ° Ð·Ð°Ð¿ÑÐ¾ÑÐ° Ð² FreeModel OpenAI-compatible ({self.model})...")
        response = self.http_client.request("POST", self.api_url, headers=headers, json=payload)
        if response.status_code == 402:
            log("ÐÐ¨ÐÐÐÐ: ÐÐµÐ´Ð¾ÑÑÐ°ÑÐ¾ÑÐ½Ð¾ ÑÑÐµÐ´ÑÑÐ² Ð½Ð° Ð°ÐºÐºÐ°ÑÐ½ÑÐµ FreeModel (HTTP 402).")
            raise RuntimeError("Insufficient FreeModel balance")
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


class AIProviderFactory:
    """Ð¤Ð°Ð±ÑÐ¸ÐºÐ° Ð´Ð»Ñ ÑÐ¾Ð·Ð´Ð°Ð½Ð¸Ñ AI-Ð¿ÑÐ¾Ð²Ð°Ð¹Ð´ÐµÑÐ°."""

    @staticmethod
    def create(api_type, api_key, model, api_url, http_client):
        if api_type == "anthropic":
            return AnthropicProvider(api_key, model, api_url, http_client)
        if api_type == "openai":
            return OpenAICompatibleProvider(api_key, model, api_url, http_client)
        raise ValueError(f"ÐÐµÐ¿Ð¾Ð´Ð´ÐµÑÐ¶Ð¸Ð²Ð°ÐµÐ¼ÑÐ¹ Ð¿ÑÐ¾Ð²Ð°Ð¹Ð´ÐµÑ: {api_type}")


HTTP_CLIENT = HttpClient()
AI_PROVIDER = AIProviderFactory.create(API_TYPE, API_KEY, MODEL, API_URL, HTTP_CLIENT)


def github_request(method, url, **kwargs):
    """ÐÑÐ¿Ð¾Ð»Ð½ÑÐµÑ Ð·Ð°Ð¿ÑÐ¾Ñ Ðº GitHub API Ñ Ð¿Ð¾Ð²ÑÐ¾ÑÐ½ÑÐ¼Ð¸ Ð¿Ð¾Ð¿ÑÑÐºÐ°Ð¼Ð¸."""
    return HTTP_CLIENT.request(method, url, headers=HEADERS_GH, **kwargs)


def get_repo_files():
    """ÐÐ¾Ð»ÑÑÐ°ÐµÐ¼ ÑÐ¿Ð¸ÑÐ¾Ðº ÑÐ°Ð¹Ð»Ð¾Ð² Ð² ÑÐµÐ¿Ð¾Ð·Ð¸ÑÐ¾ÑÐ¸Ð¸ ÑÐµÑÐµÐ· GitHub API."""
    url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/git/trees/HEAD?recursive=1"
    resp = github_request("GET", url)
    resp.raise_for_status()
    data = resp.json()
    
    files = []
    for item in data.get("tree", []):
        if item["type"] == "blob":
            path = item["path"]
            if any(path.endswith(ext) for ext in [
                ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs",
                ".c", ".cpp", ".h", ".md", ".yml", ".yaml", ".json"
            ]):
                if item.get("size", 0) < MAX_FILE_SIZE:
                    files.append(path)
    
    log(f"ÐÐ°Ð¹Ð´ÐµÐ½Ð¾ {len(files)} ÑÐ°Ð¹Ð»Ð¾Ð² Ð´Ð»Ñ Ð°Ð½Ð°Ð»Ð¸Ð·Ð°")
    return files[:MAX_FILES_TO_SCAN]


def get_file_content(path):
    """ÐÐ¾Ð»ÑÑÐ°ÐµÐ¼ ÑÐ¾Ð´ÐµÑÐ¶Ð¸Ð¼Ð¾Ðµ ÑÐ°Ð¹Ð»Ð°."""
    url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{path}"
    resp = github_request("GET", url)
    if resp.status_code != 200:
        return None
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
    return content


def find_todos_in_files(files):
    """ÐÑÐµÐ¼ ÑÐ°Ð¹Ð»Ñ Ñ TODO/FIXME Ð´Ð»Ñ Ð¿ÑÐ¸Ð¾ÑÐ¸ÑÐµÑÐ°."""
    prioritized = []
    for file_path in files:
        content = get_file_content(file_path)
        if content and re.search(r"(TODO|FIXME|XXX|HACK|BUG)", content, re.I):
            prioritized.append(file_path)
    return prioritized


def build_context(files):
    """Ð¡ÑÑÐ¾Ð¸Ð¼ ÐºÐ¾Ð½ÑÐµÐºÑÑ Ð´Ð»Ñ AI."""
    context_parts = []
    for file_path in files:
        content = get_file_content(file_path)
        if content:
            context_parts.append(f"\n--- FILE: {file_path} ---\n{content}\n")
    return "".join(context_parts)


def get_ci_logs():
    """ÐÐ¾Ð»ÑÑÐ°ÐµÐ¼ Ð»Ð¾Ð³Ð¸ ÑÐ¿Ð°Ð²ÑÐµÐ³Ð¾ CI (ÐµÑÐ»Ð¸ Ð·Ð°Ð¿ÑÑÐµÐ½Ð¾ Ð¿Ð¾ÑÐ»Ðµ failure)."""
    run_id = os.environ.get("RUN_ID", "")
    if not run_id:
        return ""
    
    jobs_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/actions/runs/{run_id}/jobs"
    response = github_request("GET", jobs_url)
    if response.status_code == 200:
        jobs = response.json().get("jobs", [])
        logs = []
        for job in jobs:
            if job.get("conclusion") == "failure":
                steps = job.get("steps", [{}])
                failed_steps = [step for step in steps if step.get("conclusion") == "failure"]
                if failed_steps:
                    logs.append(f"Job '{job['name']}' failed at step: {failed_steps[0].get('name', 'unknown')}")
        return "\n".join(logs) if logs else ""
    return ""


def call_ai(prompt):
    """ÐÑÐ¿ÑÐ°Ð²Ð»ÑÐµÐ¼ Ð·Ð°Ð¿ÑÐ¾Ñ Ð² AI API ÑÐµÑÐµÐ· Ð²ÑÐ±ÑÐ°Ð½Ð½Ð¾Ð³Ð¾ Ð¿ÑÐ¾Ð²Ð°Ð¹Ð´ÐµÑÐ°."""
    content = AI_PROVIDER.generate(prompt)
    log("ÐÑÐ²ÐµÑ Ð¿Ð¾Ð»ÑÑÐµÐ½")
    return content


def parse_changes(ai_response):
    """ÐÐ°ÑÑÐ¸Ð¼ JSON Ñ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸ÑÐ¼Ð¸ Ð¸Ð· Ð¾ÑÐ²ÐµÑÐ° AI."""
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', ai_response, re.DOTALL)
    if json_match:
        ai_response = json_match.group(1)
    
    try:
        data = json.loads(ai_response)
    except json.JSONDecodeError:
        match = re.search(r'(\{.*\})', ai_response, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
        else:
            raise
    
    return data.get("changes", []), data.get("analysis", "")


def create_branch_and_pr(changes, analysis):
    """Ð¡Ð¾Ð·Ð´Ð°ÑÐ¼ Ð²ÐµÑÐºÑ, ÐºÐ¾Ð¼Ð¼Ð¸ÑÐ¸Ð¼ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ñ Ð¸ ÑÐ¾Ð·Ð´Ð°ÑÐ¼ PR."""
    if not changes:
        log("ÐÐµÑ Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ð¹ Ð´Ð»Ñ ÐºÐ¾Ð¼Ð¼Ð¸ÑÐ°")
        return

    for branch in ["main", "master"]:
        url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/git/ref/heads/{branch}"
        resp = github_request("GET", url)
        if resp.status_code == 200:
            base_sha = resp.json()["object"]["sha"]
            base_branch = branch
            break
    else:
        raise Exception("ÐÐµ Ð½Ð°Ð¹Ð´ÐµÐ½Ð° Ð²ÐµÑÐºÐ° main Ð¸Ð»Ð¸ master")
    
    branch_name = f"ai/freemodel-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    create_ref_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/git/refs"
    create_ref_response = github_request(
        "POST",
        create_ref_url,
        json={
            "ref": f"refs/heads/{branch_name}",
            "sha": base_sha
        }
    )
    create_ref_response.raise_for_status()
    log(f"Ð¡Ð¾Ð·Ð´Ð°Ð½Ð° Ð²ÐµÑÐºÐ°: {branch_name}")
    time.sleep(2)
    
    for change in changes:
        file_path = change["file_path"]
        action = change.get("action", "modify")
        content = change.get("content", "")
        
        if action == "delete":
            get_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{file_path}?ref={branch_name}"
            get_response = github_request("GET", get_url)
            if get_response.status_code == 200:
                sha = get_response.json()["sha"]
                delete_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{file_path}"
                delete_response = github_request(
                    "DELETE",
                    delete_url,
                    json={
                        "message": f"ð¤ Ð£Ð´Ð°Ð»ÑÐ½ {file_path}",
                        "sha": sha,
                        "branch": branch_name
                    }
                )
                delete_response.raise_for_status()
            continue
        
        sha = None
        get_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{file_path}?ref={branch_name}"
        get_response = github_request("GET", get_url)
        if get_response.status_code == 200:
            sha = get_response.json().get("sha")
        
        put_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{file_path}"
        payload = {
            "message": f"ð¤ {action}: {file_path}",
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch_name
        }
        if sha:
            payload["sha"] = sha
        
        put_response = github_request("PUT", put_url, json=payload)
        put_response.raise_for_status()
        log(f"{'ÐÐ±Ð½Ð¾Ð²Ð»ÑÐ½' if sha else 'Ð¡Ð¾Ð·Ð´Ð°Ð½'} ÑÐ°Ð¹Ð»: {file_path}")
    
    pr_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/pulls"
    pr_body = f"""## ð¤ ÐÐ²ÑÐ¾Ð¼Ð°ÑÐ¸ÑÐµÑÐºÐ¸Ð¹ PR Ð¾Ñ AI Agent

**Ð ÐµÐ¶Ð¸Ð¼:** `{AGENT_MODE}`  
**ÐÐ¾Ð´ÐµÐ»Ñ:** `{MODEL}`  
**API:** `{API_TYPE}`

### ÐÐ½Ð°Ð»Ð¸Ð·
{analysis}

---
*Ð¡Ð¾Ð·Ð´Ð°Ð½Ð¾ Ð°Ð²ÑÐ¾Ð¼Ð°ÑÐ¸ÑÐµÑÐºÐ¸ ÑÐµÑÐµÐ· GitHub Actions*"""
    
    pr_resp = github_request(
        "POST",
        pr_url,
        json={
            "title": f"ð¤ AI: {AGENT_MODE} â {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            "body": pr_body,
            "head": branch_name,
            "base": base_branch
        }
    )
    pr_resp.raise_for_status()
    pr_data = pr_resp.json()
    log(f"Ð¡Ð¾Ð·Ð´Ð°Ð½ PR: {pr_data['html_url']}")


def main():
    log(f"ÐÐ°Ð¿ÑÑÐº AI Agent | API: {API_TYPE} | ÐÐ¾Ð´ÐµÐ»Ñ: {MODEL} | Ð ÐµÐ¶Ð¸Ð¼: {AGENT_MODE}")
    
    if not API_KEY or not GITHUB_TOKEN:
        log("ÐÐ¨ÐÐÐÐ: ÐÐµ Ð·Ð°Ð´Ð°Ð½Ñ FREEMODEL_API_KEY Ð¸Ð»Ð¸ GITHUB_TOKEN")
        return

    files = get_repo_files()
    if not files:
        log("ÐÐµÑ ÑÐ°Ð¹Ð»Ð¾Ð² Ð´Ð»Ñ Ð°Ð½Ð°Ð»Ð¸Ð·Ð°")
        return
    
    if AGENT_MODE == "auto_todo":
        todo_files = find_todos_in_files(files)
        if todo_files:
            files = todo_files[:MAX_FILES_TO_SCAN]
            log(f"ÐÑÐ¸Ð¾ÑÐ¸ÑÐ¸Ð·Ð¸ÑÐ¾Ð²Ð°Ð½Ð¾ {len(files)} ÑÐ°Ð¹Ð»Ð¾Ð² Ñ TODO/FIXME")
    
    context = build_context(files)
    
    ci_logs = get_ci_logs()
    if ci_logs:
        context += f"\n--- CI LOGS (FAILURE) ---\n{ci_logs}\n"
    
    mode_prompt = MODE_PROMPTS.get(AGENT_MODE, MODE_PROMPTS["auto_todo"])
    prompt = f"""{mode_prompt}

ÐÐ¾Ð´Ð¾Ð²Ð°Ñ Ð±Ð°Ð·Ð°:
{context}

ÐÐµÑÐ½Ð¸ ÑÐµÐ·ÑÐ»ÑÑÐ°Ñ Ð¡Ð¢Ð ÐÐÐ Ð² ÑÐ¾ÑÐ¼Ð°ÑÐµ JSON:
{{
  \"analysis\": \"ÐºÑÐ°ÑÐºÐ¸Ð¹ Ð°Ð½Ð°Ð»Ð¸Ð· ÑÐ¾Ð³Ð¾, ÑÑÐ¾ Ð±ÑÐ»Ð¾ Ð½Ð°Ð¹Ð´ÐµÐ½Ð¾\",
  \"changes\": [
    {{
      \"file_path\": \"Ð¿ÑÑÑ/Ðº/ÑÐ°Ð¹Ð»Ñ.py\",
      \"action\": \"modify\",
      \"content\": \"Ð¿Ð¾Ð»Ð½Ð¾Ðµ Ð½Ð¾Ð²Ð¾Ðµ ÑÐ¾Ð´ÐµÑÐ¶Ð¸Ð¼Ð¾Ðµ ÑÐ°Ð¹Ð»Ð°\"
    }}
  ]
}}"""
    
    try:
        ai_response = call_ai(prompt)
    except Exception as error:
        log(f"ÐÑÐ¸Ð±ÐºÐ° Ð¿ÑÐ¸ Ð²ÑÐ·Ð¾Ð²Ðµ AI API: {error}")
        return
    
    try:
        changes, analysis = parse_changes(ai_response)
    except Exception as error:
        log(f"ÐÑÐ¸Ð±ÐºÐ° Ð¿Ð°ÑÑÐ¸Ð½Ð³Ð° Ð¾ÑÐ²ÐµÑÐ°: {error}")
        log(f"Ð¡ÑÑÐ¾Ð¹ Ð¾ÑÐ²ÐµÑ:\n{ai_response[:1000]}...")
        return
    
    log(f"ÐÐ½Ð°Ð»Ð¸Ð·: {analysis[:200]}...")
    log(f"ÐÐ·Ð¼ÐµÐ½ÐµÐ½Ð¸Ð¹: {len(changes)}")
    
    try:
        create_branch_and_pr(changes, analysis)
    except Exception as error:
        log(f"ÐÑÐ¸Ð±ÐºÐ° Ð¿ÑÐ¸ ÑÐ¾Ð·Ð´Ð°Ð½Ð¸Ð¸ PR: {error}")
        raise
    
    log("Ð Ð°Ð±Ð¾ÑÐ° Ð·Ð°Ð²ÐµÑÑÐµÐ½Ð°!")


if __name__ == "__main__":
    main()
