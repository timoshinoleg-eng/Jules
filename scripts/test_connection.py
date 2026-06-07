#!/usr/bin/env python3
"""
Тестовый скрипт для проверки подключения к FreeModel API и GitHub API
Запускай локально перед деплоем в GitHub Actions.

Использование:
    export FREEMODEL_API_KEY="fe_oa_..."
    export GITHUB_TOKEN="ghp_..."
    export REPO_FULL_NAME="username/repo"
    export API_TYPE="anthropic"  # anthropic (Claude) или openai (GPT)
    python scripts/test_connection.py
"""

import os
import sys
import requests

FREEMODEL_API_KEY = os.environ.get("FREEMODEL_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO_FULL_NAME = os.environ.get("REPO_FULL_NAME", "")
API_TYPE = os.environ.get("API_TYPE", "anthropic")
MODEL = os.environ.get("MODEL", "")

def test_freemodel():
    """Тест подключения к FreeModel API."""
    print("=" * 50)
    print("ТЕСТ 1: Подключение к FreeModel API")
    print("=" * 50)
    
    if not FREEMODEL_API_KEY:
        print("❌ FREEMODEL_API_KEY не задан")
        return False
    
    if API_TYPE == "anthropic":
        url = "https://cc.freemodel.dev/v1/messages"
        headers = {
            "x-api-key": FREEMODEL_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        model = MODEL or "claude-opus-4-20250514"
        payload = {
            "model": model,
            "max_tokens": 256,
            "messages": [{"role": "user", "content": "Скажи 'Привет, FreeModel работает!' на русском."}]
        }
        api_name = "Claude (Anthropic-compatible)"
    else:
        url = "https://api.freemodel.dev/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {FREEMODEL_API_KEY}",
            "Content-Type": "application/json"
        }
        model = MODEL or "gpt-5.5"
        payload = {
            "model": model,
            "max_tokens": 256,
            "messages": [
                {"role": "system", "content": "Ты помощник."},
                {"role": "user", "content": "Скажи 'Привет, FreeModel работает!' на русском."}
            ]
        }
        api_name = "OpenAI-compatible"
    
    print(f"  Endpoint: {url}")
    print(f"  API тип: {api_name}")
    print(f"  Модель: {model}")
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            if API_TYPE == "anthropic":
                content = data.get("content", [{}])[0].get("text", "")
            else:
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"✅ Успешно! Ответ: {content[:100]}...")
            print(f"   Использовано токенов: {data.get('usage', {}).get('total_tokens', 'N/A')}")
            return True
        else:
            print(f"❌ Ошибка HTTP {resp.status_code}")
            print(f"   Ответ: {resp.text[:500]}")
            return False
    except Exception as e:
        print(f"❌ Исключение: {e}")
        return False


def test_github():
    """Тест подключения к GitHub API."""
    print("\n" + "=" * 50)
    print("ТЕСТ 2: Подключение к GitHub API")
    print("=" * 50)
    
    if not GITHUB_TOKEN:
        print("❌ GITHUB_TOKEN не задан")
        return False
    
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # Проверяем, кто мы
    try:
        resp = requests.get("https://api.github.com/user", headers=headers, timeout=30)
        if resp.status_code == 200:
            user = resp.json()
            print(f"✅ Авторизация успешна")
            print(f"   Пользователь: {user.get('login')}")
        else:
            print(f"❌ Ошибка авторизации: HTTP {resp.status_code}")
            print(f"   {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"❌ Исключение: {e}")
        return False
    
    # Проверяем репозиторий
    if REPO_FULL_NAME:
        print(f"\n  Проверка репозитория: {REPO_FULL_NAME}")
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{REPO_FULL_NAME}",
                headers=headers, timeout=30
            )
            if resp.status_code == 200:
                repo = resp.json()
                print(f"✅ Репозиторий доступен: {repo.get('full_name')}")
                print(f"   Приватный: {repo.get('private')}")
                print(f"   Default branch: {repo.get('default_branch')}")
                
                # Проверяем права на создание PR
                print(f"\n  Проверка прав на создание PR...")
                resp_perm = requests.get(
                    f"https://api.github.com/repos/{REPO_FULL_NAME}/collaborators",
                    headers=headers, timeout=30
                )
                if resp_perm.status_code == 200:
                    print(f"✅ Есть доступ к collaborator'ам (достаточно для PR)")
                else:
                    print(f"⚠️ Нет доступа к collaborator'ам, но PR может всё равно работать")
                return True
            else:
                print(f"❌ Репозиторий недоступен: HTTP {resp.status_code}")
                return False
        except Exception as e:
            print(f"❌ Исключение: {e}")
            return False
    else:
        print("⚠️ REPO_FULL_NAME не задан — пропускаем проверку репозитория")
        return True


def test_repo_files():
    """Тест получения списка файлов из репо."""
    if not REPO_FULL_NAME:
        return True
    
    print("\n" + "=" * 50)
    print("ТЕСТ 3: Получение файлов из репозитория")
    print("=" * 50)
    
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        url = f"https://api.github.com/repos/{REPO_FULL_NAME}/git/trees/HEAD?recursive=1"
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            files = [item["path"] for item in resp.json().get("tree", []) if item["type"] == "blob"]
            py_files = [f for f in files if f.endswith(".py")]
            print(f"✅ Получено {len(files)} файлов, {len(py_files)} Python-файлов")
            if py_files:
                print(f"   Примеры: {', '.join(py_files[:3])}")
            return True
        else:
            print(f"❌ Ошибка: HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"❌ Исключение: {e}")
        return False


def main():
    print("🔧 Тестирование подключений для AI Agent")
    print(f"   API_TYPE: {API_TYPE}")
    print(f"   REPO: {REPO_FULL_NAME or '(не задан)'}")
    
    results = []
    results.append(("FreeModel API", test_freemodel()))
    results.append(("GitHub API", test_github()))
    results.append(("GitHub Repo Files", test_repo_files()))
    
    print("\n" + "=" * 50)
    print("ИТОГИ")
    print("=" * 50)
    for name, ok in results:
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status} — {name}")
    
    all_ok = all(ok for _, ok in results)
    if all_ok:
        print("\n🎉 Все тесты пройдены! Можно деплоить в GitHub Actions.")
        sys.exit(0)
    else:
        print("\n⚠️  Есть проблемы. Исправь их перед деплоем.")
        sys.exit(1)


if __name__ == "__main__":
    main()
