#!/usr/bin/env python3
"""
FreeModel Auto-Coder Agent
Аналог Google Jules. Поддерживает Claude (Anthropic API) и OpenAI-compatible endpoints.
Работает в GitHub Actions.
"""

import os
import re
import json
import time
import base64
from datetime import datetime

import requests

# ==================== НАСТРОЙКИ ====================
AGENT_MODE = os.environ.get("AGENT_MODE", "auto_todo")
API_KEY = os.environ.get("FREEMODEL_API_KEY", "")
GH_PAT = os.environ.get("GH_PAT", "")  # Personal Access Token with full repo: scope
GITHUB_TOKEN = GH_PAT or os.environ.get("GITHUB_TOKEN", "")  # Prefer PAT for PR creation
REPO_FULL_NAME = os.environ.get("REPO_FULL_NAME", "")
MAX_FILES_TO_SCAN = 15
MAX_FILE_SIZE = 50000
MAX_TOKENS = 4000
REQUEST_TIMEOUT = 60
MAX_RETRIES = 3
BACKOFF_FACTOR = 1.5
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}

# Выбор API-провайдера. Поддерживаются алиасы для совместимости.
RAW_API_TYPE = os.environ.get("API_TYPE", "openai")
API_TYPE_ALIASES = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "gpt": "openai",
}
API_TYPE = API_TYPE_ALIASES.get(RAW_API_TYPE.strip().lower(), "openai")

AI_PROVIDERS = {
    "anthropic": {
        "base_url": "https://cc.freemodel.dev",
        "api_url": "https://cc.freemodel.dev/v1/messages",
        "default_model": "claude-opus-4-20250514",
        "display_name": "Claude через FreeModel",
    },
    "openai": {
        "base_url": "https://api.freemodel.dev/v1",
        "api_url": "https://api.freemodel.dev/v1/chat/completions",
        "default_model": "gpt-5.4",
        "display_name": "OpenAI-compatible через FreeModel",
    },
}

PROVIDER_CONFIG = AI_PROVIDERS[API_TYPE]
BASE_URL = PROVIDER_CONFIG["base_url"]
MODEL = os.environ.get("MODEL", PROVIDER_CONFIG["default_model"])
API_URL = PROVIDER_CONFIG["api_url"]

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


def should_retry_response(response):
    """Определяет, нужно ли повторить запрос по статус-коду."""
    return response.status_code in RETRYABLE_STATUS_CODES


def request_with_retry(method, url, *, headers=None, json_payload=None, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES, backoff_factor=BACKOFF_FACTOR, retry_on_statuses=None):
    """Выполняет HTTP-запрос с повторными попытками и экспоненциальной задержкой."""
    statuses_to_retry = retry_on_statuses or RETRYABLE_STATUS_CODES
    last_exception = None
    last_response = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=json_payload,
                timeout=timeout,
            )
            last_response = response

            if response.status_code not in statuses_to_retry:
                return response

            log(
                f"Повторяем {method.upper()} {url}: HTTP {response.status_code}, "
                f"попытка {attempt}/{max_retries}"
            )
        except requests.RequestException as exc:
            last_exception = exc
            log(
                f"Сетевая ошибка при {method.upper()} {url}: {exc}, "
                f"попытка {attempt}/{max_retries}"
            )

        if attempt < max_retries:
            sleep_seconds = backoff_factor ** (attempt - 1)
            time.sleep(sleep_seconds)

    if last_response is not None:
        return last_response

    if last_exception is not None:
        raise last_exception

    raise RuntimeError(f"Не удалось выполнить запрос {method.upper()} {url}")


def github_request(method, path, *, json_payload=None, expected_statuses=None):
    """Выполняет запрос к GitHub API с retry-логикой."""
    url = path if path.startswith("http") else f"{GITHUB_API}{path}"
    response = request_with_retry(
        method=method,
        url=url,
        headers=HEADERS_GH,
        json_payload=json_payload,
    )

    if expected_statuses and response.status_code not in expected_statuses:
        response.raise_for_status()

    return response


def get_repo_files():
    """Получаем список файлов в репозитории через GitHub API."""
    resp = github_request("GET", f"/repos/{REPO_FULL_NAME}/git/trees/HEAD?recursive=1", expected_statuses={200})
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
    resp = github_request("GET", f"/repos/{REPO_FULL_NAME}/contents/{path}")
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

    response = github_request("GET", f"/repos/{REPO_FULL_NAME}/actions/runs/{run_id}/jobs")
    if response.status_code == 200:
        jobs = response.json().get("jobs", [])
        logs = []
        for job in jobs:
            if job.get("conclusion") == "failure":
                steps = job.get("steps", [{}])
                failed_step = [step for step in steps if step.get("conclusion") == "failure"]
                if failed_step:
                    logs.append(f"Job '{job['name']}' failed at step: {failed_step[0].get('name', 'unknown')}")
        return "\n".join(logs) if logs else ""
    return ""


def call_anthropic(prompt):
    """Отправляет запрос в Anthropic-совместимый API."""
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
    response = request_with_retry("POST", API_URL, headers=headers, json_payload=payload)
    response.raise_for_status()
    data = response.json()
    content = data["content"][0]["text"]
    if "access denied" in content.lower() or "restricted" in content.lower():
        log("ОШИБКА: FreeModel Claude endpoint требует официальный Claude Code CLI.")
        log(f"Тело ответа: {content[:200]}")
        raise RuntimeError(f"API заблокирован: {content[:200]}")
    return content


def call_openai_compatible(prompt):
    """Отправляет запрос в OpenAI-compatible API."""
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
    response = request_with_retry("POST", API_URL, headers=headers, json_payload=payload)
    if response.status_code == 402:
        log("ОШИБКА: Недостаточно средств на аккаунте FreeModel (HTTP 402).")
        raise RuntimeError("Insufficient FreeModel balance")
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def call_ai(prompt):
    """Отправляем запрос в AI API через выбранного провайдера."""
    provider_handlers = {
        "anthropic": call_anthropic,
        "openai": call_openai_compatible,
    }

    handler = provider_handlers.get(API_TYPE)
    if handler is None:
        raise ValueError(f"Неподдерживаемый AI-провайдер: {API_TYPE}")

    content = handler(prompt)
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

    base_sha = None
    base_branch = None
    for branch in ["main", "master"]:
        response = github_request("GET", f"/repos/{REPO_FULL_NAME}/git/ref/heads/{branch}")
        if response.status_code == 200:
            base_sha = response.json()["object"]["sha"]
            base_branch = branch
            break

    if not base_sha or not base_branch:
        raise Exception("Не найдена ветка main или master")

    branch_name = f"ai/freemodel-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    github_request(
        "POST",
        f"/repos/{REPO_FULL_NAME}/git/refs",
        json_payload={
            "ref": f"refs/heads/{branch_name}",
            "sha": base_sha
        },
        expected_statuses={201},
    )
    log(f"Создана ветка: {branch_name}")
    time.sleep(2)

    for change in changes:
        file_path = change["file_path"]
        action = change.get("action", "modify")
        content = change.get("content", "")

        if action == "delete":
            current_file_response = github_request(
                "GET",
                f"/repos/{REPO_FULL_NAME}/contents/{file_path}?ref={branch_name}"
            )
            if current_file_response.status_code == 200:
                sha = current_file_response.json()["sha"]
                github_request(
                    "DELETE",
                    f"/repos/{REPO_FULL_NAME}/contents/{file_path}",
                    json_payload={
                        "message": f"🤖 Удалён {file_path}",
                        "sha": sha,
                        "branch": branch_name
                    },
                    expected_statuses={200},
                )
            continue

        sha = None
        current_file_response = github_request(
            "GET",
            f"/repos/{REPO_FULL_NAME}/contents/{file_path}?ref={branch_name}"
        )
        if current_file_response.status_code == 200:
            sha = current_file_response.json().get("sha")

        payload = {
            "message": f"🤖 {action}: {file_path}",
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch_name
        }
        if sha:
            payload["sha"] = sha

        put_response = github_request(
            "PUT",
            f"/repos/{REPO_FULL_NAME}/contents/{file_path}",
            json_payload=payload,
            expected_statuses={200, 201},
        )
        if put_response.status_code in (200, 201):
            log(f"{'Обновлён' if sha else 'Создан'} файл: {file_path}")

    pr_body = f"""## 🤖 Автоматический PR от AI Agent

**Режим:** `{AGENT_MODE}`  
**Модель:** `{MODEL}`  
**API:** `{API_TYPE}`

### Анализ
{analysis}

---
*Создано автоматически через GitHub Actions*"""

    pr_response = github_request(
        "POST",
        f"/repos/{REPO_FULL_NAME}/pulls",
        json_payload={
            "title": f"🤖 AI: {AGENT_MODE} — {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            "body": pr_body,
            "head": branch_name,
            "base": base_branch
        },
        expected_statuses={201},
    )
    pr_data = pr_response.json()
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
    except Exception as exc:
        log(f"Ошибка при вызове AI API: {exc}")
        return

    try:
        changes, analysis = parse_changes(ai_response)
    except Exception as exc:
        log(f"Ошибка парсинга ответа: {exc}")
        log(f"Сырой ответ:\n{ai_response[:1000]}...")
        return

    log(f"Анализ: {analysis[:200]}...")
    log(f"Изменений: {len(changes)}")

    try:
        create_branch_and_pr(changes, analysis)
    except Exception as exc:
        log(f"Ошибка при создании PR: {exc}")
        raise

    log("Работа завершена!")


if __name__ == "__main__":
    main()
