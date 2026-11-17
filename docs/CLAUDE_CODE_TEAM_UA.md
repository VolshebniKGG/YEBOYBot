# Налаштування Claude Code та GitHub для YEBOYBot

## Авторизація на Windows
Підключення GitHub до ChatGPT **не авторизує** локальний Claude Code. Виконуй у PowerShell поза Claude:

```powershell
git --version
gh --version
# Якщо gh відсутній:
winget install --id GitHub.cli -e --source winget
# Після інсталяції відкрий новий PowerShell:
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status
gh api user --jq .login
```

Переконайся, що акаунт `VolshebniKGG`. Авторизація відбувається у браузері. Не вставляй персональні токени в код, файли чи чат.

## Вибір Git-копії та гілки
Перейди до справжньої директорії проєкту (вкладені папки ZIP можуть створювати плутанину):

```powershell
Set-Location 'G:\Bot\YEBOYBot-improvement-probot-readiness'
git rev-parse --show-toplevel
git remote -v
git status --short --branch
```

Якщо це не Git-копія, клонуй у **нову порожню** папку:

```powershell
git clone --branch improvement/probot-readiness https://github.com/VolshebniKGG/YEBOYBot.git 'G:\Bot\YEBOYBot-work'
Set-Location 'G:\Bot\YEBOYBot-work'
```

Перевір, що `origin` вказує на `https://github.com/VolshebniKGG/YEBOYBot.git`. У чистій Git-копії:

```powershell
git fetch origin
git switch improvement/probot-readiness
git pull --ff-only origin improvement/probot-readiness
fcc-claude
```

Якщо гілки немає локально, виконай `git switch --track origin/improvement/probot-readiness` замість звичайного `git switch`. Якщо є незбережені зміни або конфлікти — не відкидай їх, спочатку розбери стан.

## Агенти
У `.claude/agents/` налаштовано:
- `architecture-reviewer`: архітектура, read-only.
- `windows-launcher-engineer`: Windows CMD/PowerShell, встановлення, запуск.
- `python-discord-engineer`: Core V2, Discord, музика, модерація, БД.
- `security-auditor`: безпека, read-only.
- `qa-test-engineer`: тести і регресії.
- `git-release-manager`: контроль публікації у GitHub.

Всі мають `model: inherit` і успадковують модель поточного FCC, а не запускають шість різних LLM. У Claude попроси: «Прочитай CLAUDE.md, залучай відповідних субагентів, відповідай українською».

## Публікація
Claude може редагувати код локально, але commit і push виконує **лише після твого явного прохання**. Перевіряй:

```powershell
git status --short --branch
git diff --check
git diff --stat
```

Для нових власних правок явно вибирай файли, після чого (якщо тести пройдені):

```powershell
git add -- path/to/changed-file.py
git diff --cached --check
git diff --cached --stat
git commit -m "fix: describe verified change"
git push -u origin improvement/probot-readiness
```

Приклад `path/to/changed-file.py` заміни на справжній файл. Не пуш `main`; не виконуй `git add -A`, `--force`, `reset --hard`. Створюй draft PR на перевірку власнику, merge тільки після погодження і зеленого CI. Для реального блокування прямого push в `main` у GitHub Settings → Rules → Rulesets налаштуй вимогу Pull Request і успішного CI: `CLAUDE.md` — інструкція, не технічний захист.

## Розширення — лише за потреби
- Офіційний `claude-code-setup`: у Claude `/plugin install claude-code-setup@claude-plugins-official`. Це порадник щодо автоматизації.
- `claude-mem`: `/plugin marketplace add thedotmack/claude-mem`, потім `/plugin install claude-mem`; перезапусти. Оціни збереження історії, телеметрію та умови підписки.
- `Strix`: окремий інструмент пентестингу з Docker/LLM API; не запускай на бойових системах.
- `FreeLLMAPI`: агрегатор провайдерів, а не агент; FCC вже підтримує маршрутизацію моделей.
- `Agent-Reach`: доступ до зовнішніх сайтів; потенційно використовує браузерні сесії. Не встановлюй без аналізу доступів.

## Діагностика
```powershell
gh auth status
git remote -v
git status --short --branch
git ls-remote --heads origin improvement/probot-readiness
fcc-doctor
```
