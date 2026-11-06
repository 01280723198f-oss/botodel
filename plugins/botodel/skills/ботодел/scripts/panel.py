#!/usr/bin/env python3
"""Ботодел — панель ботов: http://127.0.0.1:8795 (на Маке), с телефона — ссылка с ключом из панели.

Базы ботов читает ТОЛЬКО на чтение (sqlite mode=ro), статус — из launchd.
Единственное действие — рассылка: панель вызывает сам бот (`bot.py --broadcast файл [--test]`),
«всем» доступно только после теста того же текста. Только stdlib.

  python3 panel.py            # поднять панель
  python3 panel.py --dump     # вывести данные JSON и выйти (проверка)
"""
import hashlib, json, os, re, secrets, socket, sqlite3, subprocess, sys, tempfile, threading, time, urllib.parse, urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = f"{HOME}/.claude/secrets"
CACHE = os.path.join(HERE, "cache.json")
KEYFILE = os.path.join(HERE, ".key")        # ключ для телефона, не коммитить
CONFIG = os.environ.get("BOTS_PANEL_CONFIG", os.path.join(HERE, "bots.json"))


def load_config():
    """Список ботов и канала — в bots.json рядом с panel.py (пример: bots.example.json)."""
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    for b in cfg.get("audience", []):
        b["dir"] = os.path.expanduser(b["dir"])
        b["db"] = os.path.join(b["dir"], b.get("db", "bot.sqlite"))
        b.setdefault("kind", "basic")
    return cfg


CFG = load_config()
PORT = int(os.environ.get("BOTS_PANEL_PORT", CFG.get("port", 8795)))
OWNER = CFG.get("owner", "Автор")
AUDIENCE = CFG.get("audience", [])       # боты для подписчиков: своя база, свои люди
SERVICE = CFG.get("service", [])         # рабочие боты и задачи: важно только «живой или нет»
BOT_DIRS = {b["id"]: b["dir"] for b in AUDIENCE}
SOURCE_NAMES = {"direct": "Напрямую", "organic": "Без метки", "unknown": "Неизвестно",
                "threads": "Threads", "reels": "Рилсы", "shorts": "Shorts", "pinterest": "Pinterest",
                "vcru": "vc.ru", "seo": "Сайт (SEO)", "vk": "ВКонтакте", "tg": "Telegram",
                **CFG.get("sources", {})}


def source_name(s):
    s = s or "direct"
    base, _, tail = s.partition("-")
    name = SOURCE_NAMES.get(base, base)
    return f"{name} · {tail}" if tail else name


# ---------- окружение ----------
def read_env(name):
    out = {}
    try:
        for line in open(os.path.join(SECRETS, name), encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def launchd_status():
    """{label: {"loaded": bool, "pid": int|None, "exit": int|None}}"""
    st = {}
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return st
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 3:
            pid, code, label = parts
            st[label] = {"loaded": True, "pid": int(pid) if pid.isdigit() else None,
                         "exit": int(code) if code.lstrip("-").isdigit() else None}
    return st


def usernames():
    """@имена ботов через getMe, кешируются в файл (токены в панель не уходят)."""
    try:
        cache = json.load(open(CACHE))
    except Exception:
        cache = {}
    changed = False
    for b in AUDIENCE + SERVICE:
        if not b.get("env") or cache.get(b["id"]):
            continue
        tok = read_env(b["env"]).get(b["token"])
        if not tok:
            continue
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getMe", timeout=6) as r:
                cache[b["id"]] = json.load(r)["result"]["username"]
                changed = True
        except Exception:
            pass
    if changed:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(cache, open(CACHE, "w"))
    return cache


# ---------- базы ----------
def ro(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
    con.row_factory = sqlite3.Row
    return con


def q(con, sql, *args):
    try:
        return [dict(r) for r in con.execute(sql, args)]
    except sqlite3.Error:
        return []


def one(con, sql, *args, default=0):
    rows = q(con, sql, *args)
    return (list(rows[0].values())[0] if rows else default) or default


def bot_json(bot, name):
    try:
        return json.load(open(os.path.join(bot["dir"], name), encoding="utf-8"))
    except Exception:
        return {}


def headline(intro):
    """Из приветствия бота — короткое имя сценария: первая содержательная фраза."""
    lines = [l.strip() for l in (intro or "").splitlines() if l.strip()]
    lines = [l for l in lines if not re.match(r"^(привет|здравствуй)", l, re.I)] or lines
    s = re.split(r"(?<=[.!?:])\s|\s—\s", lines[0])[0] if lines else ""
    s = s.rstrip(" .:!,")
    return (s[:70].rstrip(" ,") + "…") if len(s) > 70 else s


def collect():
    now = datetime.now()
    since7 = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    st = launchd_status()
    names = usernames()

    def status(label):
        s = st.get(label)
        if not s:
            return "off"
        return "online" if s["pid"] else ("idle" if s["exit"] in (0, None) else "error")

    bots, people, scenarios, links, broadcasts = [], [], [], [], []
    days = {}

    for b in AUDIENCE:
        if not os.path.exists(b["db"]):
            continue
        con = ro(b["db"])
        last = max(filter(None, [one(con, "select max(last_seen) from users", default=""),
                                 one(con, "select max(at) from starts", default="")]), default="")
        bots.append({"id": b["id"], "kind": b["kind"], "title": b["title"], "username": names.get(b["id"]),
                     "status": status(b["launchd"]),
                     "users": one(con, "select count(*) from users"),
                     "blocked": one(con, "select count(*) from users where blocked=1"),
                     "new7": one(con, "select count(*) from users where first_seen>=?", since7),
                     "starts": one(con, "select count(*) from starts"),
                     "last": last})
        for u in q(con, "select user_id, username, first_name, last_name, source, first_seen, last_seen, "
                        "blocked, is_premium from users"):
            u.update(bot=b["id"], bot_title=b["title"], source_name=source_name(u.get("source")))
            people.append(u)
        for r in q(con, "select substr(at,1,10) d, count(*) n from starts group by 1"):
            days.setdefault(r["d"], {})[b["id"]] = r["n"]
        for r in q(con, "select * from broadcasts order by at desc"):
            r.update(bot=b["id"], bot_title=b["title"])
            broadcasts.append(r)

        if b["kind"] == "gift":                       # шасси скилла «бот-подарок»
            cfg, drip = bot_json(b, "gifts.json"), bot_json(b, "drip.json")
            camps = cfg.get("campaigns", {})
            starts = {r["source"]: r["n"] for r in q(con, "select source, count(*) n from starts group by 1")}
            gifts = {r["source"]: r["n"] for r in q(con, "select source, count(*) n from gifts group by 1")}
            rest_s = sum(v for k, v in starts.items() if k not in camps)
            rest_g = sum(v for k, v in gifts.items() if k not in camps)
            scenarios.append({"bot": b["id"], "title": f"Подарок за подписку: {headline(cfg.get('intro'))}",
                              "key": "по умолчанию", "active": True, "starts": rest_s, "done": rest_g,
                              "done_label": "подарков"})
            for key, c in camps.items():
                sc = {"bot": b["id"], "title": headline(c.get("intro")), "key": key, "active": True,
                      "starts": starts.get(key, 0), "done": gifts.get(key, 0), "done_label": "подарков"}
                if "razbor_ok" in c:
                    sc.update(done=one(con, "select count(*) from razbor"), done_label="заявок на разбор")
                scenarios.append(sc)
            steps = [k for k in drip if k.isdigit()] if isinstance(drip, dict) else []
            scenarios.append({"bot": b["id"], "title": "Цепочка касаний после подарка", "key": "drip",
                              "active": bool(drip.get("enabled")), "starts": one(con, "select count(distinct user_id) from drip"),
                              "starts_label": "в цепочке", "done": one(con, "select count(*) from drip"),
                              "done_label": "касаний", "note": f"шагов: {len(steps)}" if steps else ""})
            scenarios.append({"bot": b["id"], "title": "Хочу свой микро-продукт", "key": "want",
                              "active": True, "starts": one(con, "select count(*) from wants"),
                              "starts_label": "нажали", "done": None})
            joins = {}
            for r in q(con, "select source, kind, count(*) n from joins group by 1,2"):
                joins.setdefault(r["source"], {})[r["kind"]] = r["n"]
            all_src = set(starts) | set(gifts) | set(joins) | {r["source"] for r in q(con, "select source from links")}
            urls = {r["source"]: r["url"] for r in q(con, "select source, url from links")}
            for s in sorted(all_src, key=lambda x: -(starts.get(x, 0) + joins.get(x, {}).get("join", 0))):
                links.append({"source": s, "name": source_name(s), "invite": urls.get(s),
                              "start": f"https://t.me/{names[b['id']]}?start={s}" if names.get(b["id"]) and s not in ("unknown", "organic") else None,
                              "starts": starts.get(s, 0), "gifts": gifts.get(s, 0),
                              "join": joins.get(s, {}).get("join", 0), "leave": joins.get(s, {}).get("leave", 0)})
        if b["kind"] == "uniq":                       # бот-обработчик с таблицей jobs
            jobs = {r["status"]: r["n"] for r in q(con, "select status, count(*) n from jobs group by 1")}
            scenarios.append({"bot": b["id"], "title": "Уникализация ролика", "key": "видео → копия",
                              "active": True, "starts": sum(jobs.values()), "starts_label": "роликов",
                              "done": jobs.get("done", 0), "done_label": "готово",
                              "note": f"ошибок: {jobs['error']}" if jobs.get("error") else ""})
        if b["kind"] == "basic":                      # любой бот по контракту: старты по меткам
            by_src = {r["source"] or "direct": r["n"] for r in q(con, "select source, count(*) n from starts group by 1")}
            scenarios.append({"bot": b["id"], "title": f"{b['title']}: старт", "key": "/start", "active": True,
                              "starts": sum(by_src.values()), "done": one(con, "select count(*) from users where blocked=0"),
                              "done_label": "на связи"})
            for s in sorted(by_src, key=lambda x: -by_src[x]):
                links.append({"source": s, "name": f"{source_name(s)} → {b['title']}", "invite": None,
                              "start": f"https://t.me/{names[b['id']]}?start={s}" if names.get(b["id"]) else None,
                              "starts": by_src[s], "gifts": 0, "join": 0, "leave": 0})
        con.close()

    service = [{"id": s["id"], "title": s["title"], "username": names.get(s["id"]), "what": s["what"],
                "status": status(s["launchd"]), "job": s.get("job", False)} for s in SERVICE]

    uniq_people = {p["user_id"] for p in people}
    chart = []
    for i in range(29, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        v = days.get(d, {})
        chart.append({"d": d, "by": {b["id"]: v.get(b["id"], 0) for b in AUDIENCE}})
    people.sort(key=lambda p: p.get("last_seen") or "", reverse=True)
    ch = channel_info()
    via = next((b for b in AUDIENCE if b["id"] == (CFG.get("channel") or {}).get("via_bot")), None)
    if ch and via and os.path.exists(via["db"]):
        con = ro(via["db"])
        ch["join7"] = one(con, "select count(*) from joins where kind='join' and at>=?", since7)
        ch["leave7"] = one(con, "select count(*) from joins where kind='leave' and at>=?", since7)
        con.close()
    return {"now": now.strftime("%Y-%m-%dT%H:%M:%S"), "owner": OWNER, "bots": bots, "service": service, "channel": ch,
            "scenarios": scenarios, "links": links, "broadcasts": broadcasts, "people": people,
            "chart": chart, "jobs": list(JOBS.values())[-10:],
            "kpi": {"people": len(uniq_people), "new7": len({p["user_id"] for p in people if (p.get("first_seen") or "") >= since7}),
                    "blocked": sum(1 for p in people if p.get("blocked")),
                    "starts30": sum(sum(c["by"].values()) for c in chart),
                    "join": sum(l["join"] for l in links), "leave": sum(l["leave"] for l in links)}}


_CH = {"at": 0, "data": None}


def channel_info():
    """Канал: число подписчиков через бота-админа канала (channel.via_bot). Кеш 10 минут."""
    if time.time() - _CH["at"] < 600:
        return dict(_CH["data"]) if _CH["data"] else None
    ch = CFG.get("channel") or {}
    via = next((b for b in AUDIENCE if b["id"] == ch.get("via_bot")), None)
    if not ch.get("username") or not via:
        return None
    name = ch["username"].lstrip("@")
    tok = read_env(via["env"]).get(via["token"])
    data = None
    if tok:
        try:
            def call(method):
                url = f"https://api.telegram.org/bot{tok}/{method}?chat_id=@{name}"
                with urllib.request.urlopen(url, timeout=6) as r:
                    return json.load(r)["result"]
            data = {"username": name, "title": call("getChat").get("title") or name,
                    "members": call("getChatMemberCount"), "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
        except Exception:
            data = dict(_CH["data"], stale=True) if _CH["data"] else None
    _CH.update(at=time.time(), data=data)
    return dict(data) if data else None


# ---------- рассылка ----------
JOBS = {}            # id → {id, bot, test, status running|done|error, out, at}
TESTED = {}          # bot → sha текста, который ушёл тестом: «всем» можно только его
LOCK = threading.Lock()


def text_hash(bot, text, button):
    return hashlib.sha256(json.dumps([bot, text, button], ensure_ascii=False).encode()).hexdigest()


def start_broadcast(bot, text, test, button=None):
    if bot not in BOT_DIRS:
        return {"ok": False, "error": "нет такого бота"}
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "пустой текст"}
    if button and next((b for b in AUDIENCE if b["id"] == bot), {}).get("kind") != "uniq":
        button = None                       # кнопку умеет только уникализатор
    h = text_hash(bot, text, button)
    if not test and TESTED.get(bot) != h:
        return {"ok": False, "error": "сначала отправь этот же текст тестом себе"}
    if any(j["status"] == "running" and not j["test"] for j in JOBS.values()):
        return {"ok": False, "error": "уже идёт рассылка, дождись конца"}
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="bots-panel-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    cmd = ["/usr/bin/python3", "bot.py", "--broadcast", path] + (["--test"] if test else [])
    if button:
        cmd += ["--button", button[0], button[1]]
    jid = secrets.token_hex(4)
    job = {"id": jid, "bot": bot, "test": test, "status": "running", "out": "",
           "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "preview": text[:80]}
    with LOCK:
        JOBS[jid] = job

    def run():
        try:
            r = subprocess.run(cmd, cwd=BOT_DIRS[bot], capture_output=True, text=True, timeout=3600)
            job["out"] = (r.stdout.strip() or r.stderr.strip())[-600:]
            job["status"] = "done" if r.returncode == 0 else "error"
            if r.returncode == 0 and test:
                TESTED[bot] = h
            if not test:
                TESTED.pop(bot, None)        # повторно «всем» тот же текст — только через новый тест
        except Exception as e:
            job.update(status="error", out=str(e))
        finally:
            os.unlink(path)
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True, "id": jid}


# ---------- доступ ----------
def panel_key():
    try:
        return open(KEYFILE).read().strip()
    except OSError:
        k = secrets.token_urlsafe(12)
        os.makedirs(os.path.dirname(KEYFILE), exist_ok=True)
        with open(KEYFILE, "w") as f:
            f.write(k)
        os.chmod(KEYFILE, 0o600)
        return k


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


KEY = panel_key()


class Handler(BaseHTTPRequestHandler):
    def local(self):
        # Host проверяем, чтобы чужой сайт не притворился «этим Маком» через подмену DNS.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return self.client_address[0] in ("127.0.0.1", "::1") and host in ("127.0.0.1", "localhost")

    def keyed(self, query):
        return secrets.compare_digest(urllib.parse.parse_qs(query).get("k", [""])[0], KEY)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in ("/", "/index.html"):
            # Без ключа страница открывается только на самом Маке; ключ вшивается в страницу для API.
            if not (self.local() or self.keyed(query)):
                return self.reply(404, b"not found", "text/plain")
            html = open(os.path.join(HERE, "panel.html"), encoding="utf-8").read()
            ip = lan_ip()
            phone = f"http://{ip}:{PORT}/?k={KEY}" if ip and self.local() else ""
            html = html.replace("__KEY__", KEY).replace("__PHONE__", phone).replace("__OWNER__", OWNER)
            return self.reply(200, html.encode(), "text/html; charset=utf-8")
        if not self.keyed(query):
            return self.reply(404, b"not found", "text/plain")
        if path == "/api/data":
            return self.reply(200, json.dumps(collect(), ensure_ascii=False).encode(), "application/json")
        self.reply(404, b"not found", "text/plain")

    def do_POST(self):
        path, _, query = self.path.partition("?")
        if not self.keyed(query) or path != "/api/broadcast":
            return self.reply(404, b"not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 20000)) or b"{}")
        except ValueError:
            return self.reply(400, b'{"ok":false}', "application/json")
        btn = body.get("button")
        btn = [btn[0], btn[1]] if isinstance(btn, list) and len(btn) == 2 and all(btn) \
            and str(btn[1]).startswith(("https://", "http://", "tg://")) else None
        res = start_broadcast(body.get("bot"), body.get("text"), bool(body.get("test", True)), btn)
        self.reply(200, json.dumps(res, ensure_ascii=False).encode(), "application/json")

    def reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if "--dump" in sys.argv:
        print(json.dumps(collect(), ensure_ascii=False, indent=1))
        sys.exit()
    # Слушает Wi-Fi, чтобы открыть с телефона; без ключа всё, кроме самого Мака, получает 404.
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Панель ботов: http://127.0.0.1:{PORT}  · телефон: http://{lan_ip()}:{PORT}/?k=<ключ из панели>", flush=True)
    srv.serve_forever()
