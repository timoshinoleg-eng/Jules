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
import requests
from datetime import datetime
from pathlib import Path

# ==================== НАСТРОЙКИ ====================
AGENT_MODE = os.environ.get("AGENT_MODE", "auto_todo")
API_KEY = os.environ.get("FREEMODEL_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO_FULL_NAME = os.environ.get("REPO_FULL_NAME", "")
MAX_FILES_TO_SCAN = 15
MAX_FILE_SIZE = 50000
MAX_TOKENS = 4000

# Выбор API: "anthropic" для Claude (cc.freemodel.dev) или "openai" для GPT (api.freemodel.dev)
API_TYPE = os.environ.get("API_TYPE", "anthropic")

if API_TYPE == "anthropic":
    # Claude через FreeModel
    BASE_URL = "https://cc.freemodel.dev"
    MODEL = os.environ.get("MODEL", "claude-opus-4-20250514")
    API_URL = f"{BASE_URL}/v1/messages"
else:
    # OpenAI-compatible через FreeModel
    BASE_URL = "https://api.freemodel.dev/v1"
    MODEL = os.environ.get("MODEL", "gpt-5.5")
    API_URL = f"{BASE_URL}/chat/completions"

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


def get_repo_files():
    """Получаем список файлов в репозитории через GitHub API."""
    url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/git/trees/HEAD?recursive=1"
    resp = requests.get(url, headers=HEADERS_GH)
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
    resp = requests.get(url, headers=HEADERS_GH)
    if resp.status_code != 200:
        return None
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
    return content


def find_todos_in_files(files):
    """Ищем файлы с TODO/FIXME для приоритета."""
    prioritized = []
    for f in files:
        content = get_file_content(f)
        if content and re.search(r"(TODO|FIXME|XXX|HACK|BUG)", content, re.I):
            prioritized.append(f)
    return prioritized


def build_context(files):
    """Строим контекст для AI."""
    context = ""
    for f in files:
        content = get_file_content(f)
        if content:
            context += f"\n--- FILE: {f} ---\n{content}\n"
    return context


def get_ci_logs():
    """Получаем логи упавшего CI (если запущено после failure)."""
    run_id = os.environ.get("RUN_ID", "")
    if not run_id:
        return ""
    
    jobs_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/actions/runs/{run_id}/jobs"
    jresp = requests.get(jobs_url, headers=HEADERS_GH)
    if jresp.status_code == 200:
        jobs = jresp.json().get("jobs", [])
        logs = []
        for job in jobs:
            if job.get("conclusion") == "failure":
                steps = job.get("steps", [{}])
                failed_step = [s for s in steps if s.get("conclusion") == "failure"]
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
        resp = requests.post(API_URL, headers=headers, json=payload)
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
        resp = requests.post(API_URL, headers=headers, json=payload)
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
    
    # Получаем SHA последнего коммита
    for branch in ["main", "master"]:
        url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/git/ref/heads/{branch}"
        resp = requests.get(url, headers=HEADERS_GH)
        if resp.status_code == 200:
            base_sha = resp.json()["object"]["sha"]
            base_branch = branch
            break
    else:
        raise Exception("Не найдена ветка main или master")
    
    branch_name = f"ai/freemodel-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    create_ref_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/git/refs"
    requests.post(create_ref_url, headers=HEADERS_GH, json={
        "ref": f"refs/heads/{branch_name}",
        "sha": base_sha
    }).raise_for_status()
    log(f"Создана ветка: {branch_name}")
    
    for change in changes:
        file_path = change["file_path"]
        action = change.get("action", "modify")
        content = change.get("content", "")
        
        if action == "delete":
            get_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{file_path}?ref={branch_name}"
            gresp = requests.get(get_url, headers=HEADERS_GH)
            if gresp.status_code == 200:
                sha = gresp.json()["sha"]
                del_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{file_path}"
                requests.delete(del_url, headers=HEADERS_GH, json={
                    "message": f"🤖 Удалён {file_path}",
                    "sha": sha,
                    "branch": branch_name
                })
            continue
        
        sha = None
        get_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/contents/{file_path}?ref={branch_name}"
        gresp = requests.get(get_url, headers=HEADERS_GH)
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
        
        requests.put(put_url, headers=HEADERS_GH, json=payload).raise_for_status()
        log(f"{'Обновлён' if sha else 'Создан'} файл: {file_path}")
    
    pr_url = f"{GITHUB_API}/repos/{REPO_FULL_NAME}/pulls"
    pr_body = f"""## 🤖 Автоматический PR от AI Agent

**Режим:** `{AGENT_MODE}`  
**Модель:** `{MODEL}`  
**API:** `{API_TYPE}`

### Анализ
{analysis}

---
*Создано автоматически через GitHub Actions*"""
    
    pr_resp = requests.post(pr_url, headers=HEADERS_GH, json={
        "title": f"🤖 AI: {AGENT_MODE} — {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        "body": pr_body,
        "head": branch_name,
        "base": base_branch
    })
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
