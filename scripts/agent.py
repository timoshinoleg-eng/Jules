#!/usr/bin/env python3
"""
FreeModel Auto-Coder Agent
Аналог Google Jules. Поддерживает Claude (Anthropic API) и OpenAI-compatible endpoints.
Работает в GitHub Actions.
"""

import os
import re
import json
import base64
import time
from datetime import datetime

import requests
from requests import Response
from requests.exceptions import RequestException

# ==================== НАСТРОЙКИ ====================
AGENT_MODE = os.environ.get("AGENT_MODE", "auto_todo")
API_KEY = os.environ.get("FREEMODEL_API_KEY", "")
GH_PAT = os.environ.get("GH_PAT", "")  # Personal Access Token with full repo: scope
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "") or GH_PAT  # Fallback to PAT if GITHUB_TOKEN empty
REPO_FULL_NAME = os.environ.get("REPO_FULL_NAME", "")
MAX_FILES_TO_SCAN = 15
MAX_FILE_SIZE = 50000
MAX_TOKENS = 4000
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
BACKOFF_FACTOR = 1.5

# Выбор API: "anthropic" для Claude (cc.freemodel.dev) или "openai" для GPT (api.freemodel.dev)
API_TYPE = os.environ.get("API_TYPE", "openai")
AI_PROVIDER = os.environ.get("AI_PROVIDER", API_TYPE).strip().lower()


# ==================== ПРОМПТЫ ====================
SYSTEM_PROMPT = """Ты — senior software engineer и AI-ассистент для автоматизации разработки.
Твоя задача — анализировать кодовую базу и предлагать конкретные изменения.

Правила:
1. Отвечай ТОЛЬКО в формате JSON с полями: "analysis" (анализ), "changes" (список изменений)
2. Каждое изменение должно содержать: "file_path", "action" (create|modify|delete), "content" (полное содержимое файла)
3. Если изменений не требуется — верни пустой changes []
4. Не используй markdown внутри JSON
5. Код должен быть рабочим, без плейсхолдеров
6. Комментарии пиши на русском языке
7. Следуй лучшим практикам: чистый код, DRY, SOLID
"""

MODE_PROMPTS = {
    "auto_todo": """Проанализируй кодовую базу. Найди TODO, FIXME, XXX комментарии и реализуй их.
Если найдешь незавершённую функцию (pass, NotImplementedError) — реализуй её.
Верни JSON с изменениями.""",
    
    "refactor": """Проанализируй кодовую базу. Найди:
- Дублирование кода
- Слишком длинные функции (>50 строк)
- Магические числа
- Неиспользуемые импорты/переменные
- Нарушения DRY/SOLID

Верни JSON с рефакторингом. Не меняй логику работы — только улучши код.""",
    
    "bugfix": """Проанализируй кодовую базу на наличие потенциальных багов:
- Необработанные edge cases
- Утечки ресурсов
- Race conditions
- SQL injection / XSS уязвимости
- Неправильная работа с None/null

Верни JSON с исправлениями.""",
    
    "review": """Проведи code review последних изменений. Укажи:
- Что сделано хорошо
- Что можно улучшить
- Критические замечания

Верни JSON с предлагаемыми изменениями (если есть)."""
}

# ==================== GITHUB API ====================
GITHUB_API = "https://api.github.com"
HEADERS_GH = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}


def log(msg):
    print(f"[AGENT] {msg}")


def normalize_provider(provider_name):
    """Нормализуем имя AI-провайдера и поддерживаем алиасы."""
    aliases = {
        "claude": "anthropic",
        "anthropic": "anthropic",
        "openai": "openai",
        "gpt": "openai"
    }
    normalized = aliases.get(provider_name.strip().lower())
    if not normalized:
        raise ValueError(f"Неподдерживаемый AI provider: {provider_name}")
    return normalized


def get_ai_config(provider_name):
    """Возвращаем конфигурацию для выбранного AI-провайдера."""
    provider = normalize_provider(provider_name)

    if provider == "anthropic":
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://cc.freemodel.dev")
        model = os.environ.get("MODEL", "claude-opus-4-20250514")
        api_url = f"{base_url}/v1/messages"
    else:
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.freemodel.dev/v1")
        model = os.environ.get("MODEL", "gpt-5.4")
        api_url = f"{base_url}/chat/completions"

    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_url": api_url
    }


AI_CONFIG = get_ai_config(AI_PROVIDER)
API_TYPE = AI_CONFIG["provider"]
BASE_URL = AI_CONFIG["base_url"]
MODEL = AI_CONFIG["model"]
API_URL = AI_CONFIG["api_url"]


def should_retry_status(status_code):
    """Определяем, стоит ли повторять запрос по HTTP-статусу."""
    retryable_statuses = {408, 409, 425, 429, 500, 502, 503, 504}
    return status_code in retryable_statuses



def request_with_retry(method, url, headers=None, json_data=None, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES):
    """Выполняем HTTP-запрос с повторными попытками и экспоненциальной задержкой."""
    last_exception = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=json_data,
                timeout=timeout
            )

            if should_retry_status(response.status_code) and attempt < max_retries:
                delay = BACKOFF_FACTOR ** (attempt - 1)
                log(
                    f"Временная ошибка HTTP {response.status_code} для {method} {url}. "
                    f"Повтор через {delay:.1f} сек."
                )
                time.sleep(delay)
                continue

            return response
        except RequestException as exc:
            last_exception = exc
            if attempt >= max_retries:
                break

            delay = BACKOFF_FACTOR ** (attempt - 1)
            log(
                f"Сетевая ошибка для {method} {url}: {exc}. "
                f"Повтор через {delay:.1f} сек."
            )
            time.sleep(delay)

    if last_exception:
        raise RuntimeError(f"Не удалось выполнить запрос {method} {url}: {last_exception}") from last_exception

    raise RuntimeError(f"Не удалось выполнить запрос {method} {url}")



def get_repo_files():
    """Получаем список файлов в репозитории через GitHub API."""
    url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/git/trees/HEAD?recursive=1"
    resp = request_with_retry("GET", url, headers=HEADERS_GH)
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
    
    log(f"Найдено {len(files)} файлов для анализа")
    return files[:MAX_FILES_TO_SCAN]



def get_file_content(path):
    """Получаем содержимое файла."""
    url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{path}"
    resp = request_with_retry("GET", url, headers=HEADERS_GH)
    if resp.status_code != 200:
        return None
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
    return content



def find_todos_in_files(files):
    """Ищем файлы с TODO/FIXME для приоритета."""
    prioritized = []
    for file_path in files:
        content = get_file_content(file_path)
        if content and re.search(r"(TODO|FIXME|XXX|HACK|BUG)", content, re.I):
            prioritized.append(file_path)
    return prioritized



def build_context(files):
    """Строим контекст для AI."""
    context = ""
    for file_path in files:
        content = get_file_content(file_path)
        if content:
            context += f"\n--- FILE: {file_path} ---\n{content}\n"
    return context



def get_ci_logs():
    """Получаем логи упавшего CI (если запущено после failure)."""
    run_id = os.environ.get("RUN_ID", "")
    if not run_id:
        return ""
    
    jobs_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/actions/runs/{run_id}/jobs"
    jresp = request_with_retry("GET", jobs_url, headers=HEADERS_GH)
    if jresp.status_code == 200:
        jobs = jresp.json().get("jobs", [])
        logs = []
        for job in jobs:
            if job.get("conclusion") == "failure":
                steps = job.get("steps", [{}])
                failed_step = [step for step in steps if step.get("conclusion") == "failure"]
                if failed_step:
                    logs.append(f"Job '{job['name']}' failed at step: {failed_step[0].get('name', 'unknown')}")
        return "\n".join(logs) if logs else ""
    return ""



def call_ai(prompt):
    """Отправляем запрос в AI API (Anthropic или OpenAI-compatible)."""
    if API_TYPE == "anthropic":
        headers = {
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        payload = {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}]
        }
        log(f"Отправка запроса в Claude через FreeModel ({MODEL})...")
        resp = request_with_retry("POST", API_URL, headers=headers, json_data=payload)
        resp.raise_for_status()
        data = resp.json()
        content = data["content"][0]["text"]
        if "access denied" in content.lower() or "restricted" in content.lower():
            log("ОШИБКА: FreeModel Claude endpoint требует официальный Claude Code CLI.")
            log(f"Тело ответа: {content[:200]}")
            raise RuntimeError(f"API заблокирован: {content[:200]}")
    else:
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ]
        }
        log(f"Отправка запроса в FreeModel OpenAI-compatible ({MODEL})...")
        resp = request_with_retry("POST", API_URL, headers=headers, json_data=payload)
        if resp.status_code == 402:
            log("ОШИБКА: Недостаточно средств на аккаунте FreeModel (HTTP 402).")
            raise RuntimeError("Insufficient FreeModel balance")
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    
    log("Ответ получен")
    return content



def parse_changes(ai_response):
    """Парсим JSON с изменениями из ответа AI."""
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
    """Создаём ветку, коммитим изменения и создаём PR."""
    if not changes:
        log("Нет изменений для коммита")
        return
    
    for branch in ["main", "master"]:
        url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/git/ref/heads/{branch}"
        resp = request_with_retry("GET", url, headers=HEADERS_GH)
        if resp.status_code == 200:
            base_sha = resp.json()["object"]["sha"]
            base_branch = branch
            break
    else:
        raise Exception("Не найдена ветка main или master")
    
    branch_name = f"ai/freemodel-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    create_ref_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/git/refs"
    create_ref_resp = request_with_retry(
        "POST",
        create_ref_url,
        headers=HEADERS_GH,
        json_data={
            "ref": f"refs/heads/{branch_name}",
            "sha": base_sha
        }
    )
    create_ref_resp.raise_for_status()
    log(f"Создана ветка: {branch_name}")
    time.sleep(2)
    
    for change in changes:
        file_path = change["file_path"]
        action = change.get("action", "modify")
        content = change.get("content", "")
        
        if action == "delete":
            get_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{file_path}?ref={branch_name}"
            gresp = request_with_retry("GET", get_url, headers=HEADERS_GH)
            if gresp.status_code == 200:
                sha = gresp.json()["sha"]
                del_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{file_path}"
                delete_resp = request_with_retry(
                    "DELETE",
                    del_url,
                    headers=HEADERS_GH,
                    json_data={
                        "message": f"🤖 Удалён {file_path}",
                        "sha": sha,
                        "branch": branch_name
                    }
                )
                delete_resp.raise_for_status()
            continue
        
        for attempt in range(3):
            sha = None
            get_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{file_path}?ref={branch_name}"
            gresp = request_with_retry("GET", get_url, headers=HEADERS_GH)
            if gresp.status_code == 200:
                sha = gresp.json().get("sha")
            
            put_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{file_path}"
            payload = {
                "message": f"🤖 {action}: {file_path}",
                "content": base64.b64encode(content.encode()).decode(),
                "branch": branch_name
            }
            if sha:
                payload["sha"] = sha
            
            put_resp = request_with_retry("PUT", put_url, headers=HEADERS_GH, json_data=payload)
            if put_resp.status_code in (200, 201):
                log(f"{'Обновлён' if sha else 'Создан'} файл: {file_path}")
                break
            log(f"Попытка {attempt + 1} не удалась для {file_path}: HTTP {put_resp.status_code}")
            time.sleep(1)
        else:
            raise Exception(f"Не удалось записать {file_path} после 3 попыток")
    
    pr_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/pulls"
    pr_body = f"""## 🤖 Автоматический PR от AI Agent

**Режим:** `{AGENT_MODE}`  
**Модель:** `{MODEL}`  
**API:** `{API_TYPE}`

### Анализ
{analysis}

---
*Создано автоматически через GitHub Actions*"""
    
    pr_resp = request_with_retry(
        "POST",
        pr_url,
        headers=HEADERS_GH,
        json_data={
            "title": f"🤖 AI: {AGENT_MODE} — {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            "body": pr_body,
            "head": branch_name,
            "base": base_branch
        }
    )
    pr_resp.raise_for_status()
    pr_data = pr_resp.json()
    log(f"Создан PR: {pr_data['html_url']}")



def main():
    log(f"Запуск AI Agent | API: {API_TYPE} | Модель: {MODEL} | Режим: {AGENT_MODE}")
    
    if not API_KEY or not GITHUB_TOKEN:
        log("ОШИБКА: Не заданы FREEMODEL_API_KEY или GITHUB_TOKEN")
        return
    
    files = get_repo_files()
    if not files:
        log("Нет файлов для анализа")
        return
    
    if AGENT_MODE == "auto_todo":
        todo_files = find_todos_in_files(files)
        if todo_files:
            files = todo_files[:MAX_FILES_TO_SCAN]
            log(f"Приоритизировано {len(files)} файлов с TODO/FIXME")
    
    context = build_context(files)
    
    ci_logs = get_ci_logs()
    if ci_logs:
        context += f"\n--- CI LOGS (FAILURE) ---\n{ci_logs}\n"
    
    mode_prompt = MODE_PROMPTS.get(AGENT_MODE, MODE_PROMPTS["auto_todo"])
    prompt = f"""{mode_prompt}

Кодовая база:
{context}

Верни результат СТРОГО в формате JSON:
{{
  "analysis": "краткий анализ того, что было найдено",
  "changes": [
    {{
      "file_path": "путь/к/файлу.py",
      "action": "modify",
      "content": "полное новое содержимое файла"
    }}
  ]
}}"""
    
    try:
        ai_response = call_ai(prompt)
    except Exception as e:
        log(f"Ошибка при вызове AI API: {e}")
        return
    
    try:
        changes, analysis = parse_changes(ai_response)
    except Exception as e:
        log(f"Ошибка парсинга ответа: {e}")
        log(f"Сырой ответ:\n{ai_response[:1000]}...")
        return
    
    log(f"Анализ: {analysis[:200]}...")
    log(f"Изменений: {len(changes)}")
    
    try:
        create_branch_and_pr(changes, analysis)
    except Exception as e:
        log(f"Ошибка при создании PR: {e}")
        raise
    
    log("Работа завершена!")


if __name__ == "__main__":
    main()
