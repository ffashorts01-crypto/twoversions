# twoversions.com на GitHub Actions — установка

Сайт обновляется сам в GitHub Actions: каждые 2 часа (сбор → карточки Qwen → проверки → сборка → Cloudflare Pages → IndexNow → Telegram)
и каждые 30 минут быстрая проверка на крупные события. Состояние (база статей, карточки, что уже отправлено в Telegram) хранится
**только в зашифрованном виде** в ветке `state` (файл `state.tar.gz.enc`, ключ — секрет `STATE_KEY`). Секретов в репозитории нет.

## 1. Создать репозиторий
GitHub → New repository → имя, например `twoversions` → **Public** (у публичных репозиториев минуты Actions бесплатны и без лимита;
приватному не хватит 2000 мин/мес). Пустой: без README, .gitignore и лицензии.

## 2. Загрузить код (делает бот со своего компьютера)
Один из вариантов:
- **Токен**: GitHub → Settings → Developer settings → Fine-grained tokens → доступ только к этому репозиторию,
  права *Contents: Read and write* и *Workflows: Read and write*, срок 1 день. Передайте токен боту через защищённую форму (не в чат).
- **Вход через gh**: бот запускает `gh auth login`, присылает 8-значный код — введите его на https://github.com/login/device.

Бот выполнит: `git push origin main state` (две ветки: код и зашифрованное состояние).

## 3. Добавить секреты
Репозиторий → Settings → Secrets and variables → Actions → New repository secret:

| Секрет | Откуда |
|---|---|
| `DASHSCOPE_API_KEY` | Alibaba Cloud Model Studio (тот же ключ, что сейчас) |
| `CLOUDFLARE_API_TOKEN` | Cloudflare → My Profile → API Tokens (право *Cloudflare Pages: Edit*; тот же токен) |
| `CLOUDFLARE_ACCOUNT_ID` | ID аккаунта Cloudflare (бот подскажет значение) |
| `STATE_KEY` | ключ шифрования состояния (бот передаст через защищённую форму) |
| `TELEGRAM_BOT_TOKEN` | токен @twoversions_war_bot от @BotFather |
| `TELEGRAM_OWNER_CHAT_ID` | *необязательно*: ваш chat id — тогда об ошибках придёт сообщение от бота (сначала напишите боту /start). Без него — письмо от GitHub |

Необязательные переменные (вкладка Variables): `LLM_DAILY_BUDGET_USD` (по умолчанию 1), `LLM_RUN_BUDGET_USD` (0.45).

## 4. Включить и проверить
Вкладка **Actions** → включить workflows → `update` → **Run workflow**. Через ~10 минут: зелёная галочка, сайт обновлён,
в ветке `state` новый коммит.

## 5. Переключение (важно — чтобы не было двойных постов)
1. Бот отключает свои routines (обновление каждые 2 часа и hot_check) **до** первого запуска в GitHub.
2. Бот заново собирает состояние (`bash deploy/make_repo.sh`) и пушит ветку `state` — GitHub продолжит ровно с того места.
3. Первый ручной запуск `update` (шаг 4). Дальше всё идёт по расписанию.

Остановить автопостинг в Telegram: удалить файл `data/telegram_enabled` в репозитории. Остановить всё: Actions → workflow → Disable.
Расписание задано в UTC (`20 1-23/2 * * *` = чётные часы по Берлину зимой; летом сдвинется на час — это не мешает).
