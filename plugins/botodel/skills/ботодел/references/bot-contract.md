# Контракт бота для Ботодела

Бот, сделанный по этому контракту, панель показывает без правки своего кода (`kind: "basic"`).

## Папка и запуск
```
~/.bots/<имя>/            # НЕ Рабочий стол и не Документы: launchd туда не пускают
  bot.py                  # Python 3 stdlib, long polling getUpdates, offset в kv
  bot.sqlite              # база (WAL)
~/.claude/secrets/<имя>.env   # <ИМЯ>_BOT_TOKEN=..., <ИМЯ>_ADMIN_ID=...
~/Library/LaunchAgents/com.<user>.<имя>.plist   # KeepAlive, RunAtLoad, лог в папку бота
```

## Таблицы (минимум)
```sql
CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
                    language TEXT, is_premium INTEGER, source TEXT, first_seen TEXT, last_seen TEXT,
                    blocked INTEGER DEFAULT 0);
CREATE TABLE starts (user_id INTEGER, source TEXT, at TEXT);
CREATE TABLE broadcasts (id INTEGER PRIMARY KEY, at TEXT, total INTEGER, sent INTEGER,
                         blocked INTEGER, failed INTEGER);
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);
```
Время — локальное ISO `YYYY-MM-DDTHH:MM:SS`. `source` — метка из `/start <метка>`
(ссылка `t.me/<бот>?start=<метка>`), без метки — `direct`. Источник в `users` пишется
при первом визите и не перезаписывается; каждый `/start` — строка в `starts`.

## Рассылка из командной строки
```
python3 bot.py --broadcast post.txt          # всем, у кого blocked=0
python3 bot.py --broadcast post.txt --test   # только админу
```
Текст — HTML-разметка Telegram. Пауза ~0.05 с между сообщениями, 429 → ждать `retry_after`.
Ошибка 403 → `blocked=1`, больше не слать. В конце — строка в `broadcasts` и итог в stdout.
Код выхода 0 при успехе (панель смотрит на него).

## Админ в самом боте
`/stats` — цифры базы, `/broadcast` ответом на своё сообщение — рассылка копией, только для ADMIN_ID.

## Проверка до запуска
Оффлайн-тест: подменить `tg()` заглушкой, прогнать `/start метка`, второй `/start`, блокировку
(403), рассылку `--test` — проверить строки в таблицах. Потом живой `/start` с телефона.
