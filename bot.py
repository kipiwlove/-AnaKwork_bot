#!/usr/bin/env python3
"""Telegram-бот @AnaKwork_bot: меню-визитка + приём заявок.

Стандартная библиотека Python, без зависимостей. Long polling через getUpdates.
Токен берётся из TG_TOKEN, chat_id для заявок — из OWNER_CHAT_ID
(или content.OWNER_CHAT_ID).

Запуск:
    python3 bot.py                 # обычный режим
    python3 bot.py --once          # обработать накопившиеся апдейты и выйти
    python3 bot.py --set-commands  # обновить список команд в профиле бота
"""

import base64
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import content

STATE_FILE = Path(__file__).with_name(".state.json")
API_URL = "https://api.telegram.org/bot{token}/{method}"
ALLOWED_UPDATES = ["message", "callback_query"]
USERNAME_RE = re.compile(r"^@([A-Za-z0-9_]{4,})$")

COMMANDS = [
    {"command": "start", "description": "Главное меню"},
    {"command": "help", "description": "Что я умею"},
    {"command": "id", "description": "Показать мой chat_id"},
]

ORDER_BUTTON = "Оставить заявку"

# chat_id -> {"step": int, "answers": {field: value}}
LEADS: dict[int, dict] = {}


# ---------------------------------------------------------------- Telegram API
def load_env_file() -> None:
    """Подхватить локальный .env (в git не попадает, ключи не светятся в коде)."""
    path = Path(__file__).with_name(".env")
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def token() -> str:
    value = os.environ.get("TG_TOKEN", "").strip()
    if not value:
        sys.exit("Ошибка: переменная окружения TG_TOKEN не задана.")
    return value


def owner_phone() -> str:
    """Ваш номер. Никогда не попадает в сообщения клиентам — только вам."""
    return os.environ.get("OWNER_PHONE", "").strip()


def sms_enabled() -> bool:
    """SMS работает, только если заданы все ключи Twilio и номер."""
    required = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "OWNER_PHONE")
    return all(os.environ.get(key, "").strip() for key in required)


def send_sms(text: str) -> bool:
    """SMS через Twilio REST API (без сторонних библиотек)."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    sender = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
    recipient = owner_phone()

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    body = urllib.parse.urlencode({"To": recipient, "From": sender, "Body": text}).encode()
    request = urllib.request.Request(url, data=body)
    request.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{auth_token}".encode()).decode())

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        detail = error.read().decode()[:200]
        print(f"! SMS не отправлено: HTTP {error.code} {detail}", flush=True)
        return False
    except urllib.error.URLError as error:
        print(f"! SMS не отправлено: {error.reason}", flush=True)
        return False
    except ValueError as error:
        print(f"! SMS не отправлено: некорректный ответ Twilio ({error})", flush=True)
        return False

    ok = bool(payload.get("sid"))
    print(f"SMS владельцу: {'отправлено' if ok else 'не подтверждено'}", flush=True)
    return ok


def leads_chat_id() -> int | None:
    """Куда приходят заявки: LEADS_CHAT_ID → OWNER_CHAT_ID → content.OWNER_CHAT_ID.

    Это может быть личка бота, отдельная группа «Заявки» или канал
    (id группы/канала отрицательный: -100...).
    """
    for key in ("LEADS_CHAT_ID", "OWNER_CHAT_ID"):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            print(f"! {key}='{raw}' — не число, пропускаю", flush=True)
    return content.OWNER_CHAT_ID or None


def call(method: str, **params):
    """Вызов метода Bot API. Возвращает result или бросает RuntimeError."""
    data = urllib.parse.urlencode(
        {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in params.items()}
    ).encode()
    url = API_URL.format(token=token(), method=method)
    request = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        payload = json.loads(error.read().decode() or "{}")
        raise RuntimeError(f"{method}: {payload.get('description', error)}") from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"{method}: сеть недоступна ({error.reason})") from None
    if not payload.get("ok"):
        raise RuntimeError(f"{method}: {payload.get('description')}")
    return payload.get("result")


def send_message(chat_id: int, text: str, reply_markup: dict | None = None, **extra):
    params = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
        **extra,
    }
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    return call("sendMessage", **params)


def set_commands():
    call("setMyCommands", commands=COMMANDS)
    print("Команды опубликованы:")
    for item in COMMANDS:
        print(f"  /{item['command']} — {item['description']}")


# ------------------------------------------------------------------- Клавиатуры
def main_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": "Что я делаю"}, {"text": "Цены"}],
            [{"text": "Примеры работ"}, {"text": "Сроки и правки"}],
            [{"text": "Боты в Telegram"}],
            [{"text": ORDER_BUTTON}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Выберите раздел или оставьте заявку",
    }


def section_keyboard() -> dict:
    return {"inline_keyboard": [[{"text": ORDER_BUTTON, "callback_data": "order"}]]}


# ------------------------------------------------------------------ Состояние
def load_offset() -> int:
    try:
        return int(json.loads(STATE_FILE.read_text()).get("offset", 0))
    except (OSError, ValueError, TypeError):
        return 0


def save_offset(offset: int) -> None:
    try:
        STATE_FILE.write_text(json.dumps({"offset": offset}))
    except OSError:
        pass


# --------------------------------------------------------------- Сценарий заявки
def start_lead(chat_id: int, username: str | None = None) -> None:
    LEADS[chat_id] = {"step": 0, "answers": {"username": username}}
    send_message(
        chat_id,
        content.LEAD_WELCOME.format(
            total=len(content.LEAD_STEPS),
            question=html.escape(content.LEAD_STEPS[0][1]),
        ),
        reply_markup=main_keyboard(),
    )


def continue_lead(chat_id: int, text: str) -> None:
    lead = LEADS[chat_id]
    field, _ = content.LEAD_STEPS[lead["step"]]
    lead["answers"][field] = text[:1500]
    lead["step"] += 1

    if lead["step"] < len(content.LEAD_STEPS):
        send_message(chat_id, f"<b>{html.escape(content.LEAD_STEPS[lead['step']][1])}</b>")
        return

    answers = lead["answers"]
    LEADS.pop(chat_id, None)
    deliver_lead(chat_id, answers)


def contact_markup(raw: str) -> str:
    """Контакт как текст; @ник превращаем в ссылку t.me."""
    match = USERNAME_RE.match(raw.strip())
    if match:
        nick = match.group(1)
        return f'<a href="https://t.me/{nick}">@{nick}</a>'
    return html.escape(raw)


def deliver_lead(chat_id: int, answers: dict) -> None:
    name_raw = (answers.get("name") or "").strip() or "—"
    task = html.escape((answers.get("task") or "").strip() or "—")
    contact_raw = (answers.get("contact") or "").strip()
    contact = contact_markup(contact_raw) if contact_raw else "—"

    target = leads_chat_id()
    delivered = False
    if target:
        notice = content.LEAD_NOTICE.format(
            name=html.escape(name_raw),
            task=task,
            contact=contact,
            username=f"@{answers['username']}" if answers.get("username") else f"id {chat_id}",
        )
        phone = owner_phone()
        if phone:
            notice += f"\n<b>Мой телефон для клиента:</b> {html.escape(phone)}"
        try:
            send_message(target, notice)
            delivered = True
        except RuntimeError as error:
            print(f"! заявка не доставлена (chat_id={target}): {error}", flush=True)

    if sms_enabled():
        sms_body = content.SMS_LEAD.format(
            name=name_raw,
            task=(answers.get("task") or "").strip()[:120],
            contact=(answers.get("contact") or "").strip()[:40],
        )
        send_sms(sms_body)

    reply = content.LEAD_DONE.format(
        name=html.escape(name_raw if name_raw != "—" else ""),
        contact_link=contact,
    )
    if not delivered:
        reply += "\n\n<i>Заявка не ушла автоматически — напишите мне напрямую, чтобы она не потерялась.</i>"
    send_message(chat_id, reply, reply_markup=main_keyboard())


# ------------------------------------------------------------------ Обработчики
def handle_message(message: dict) -> None:
    chat_id = message["chat"]["id"]
    sender = message.get("from") or {}
    text = (message.get("text") or "").strip()
    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""

    if command in ("/start", "/menu"):
        LEADS.pop(chat_id, None)
        name = html.escape(sender.get("first_name") or "друг")
        send_message(chat_id, content.GREETING.format(name=name), reply_markup=main_keyboard())
        return

    if command == "/help":
        send_message(chat_id, content.HELP)
        return

    if command == "/id":
        send_message(
            chat_id,
            f"chat_id этого чата: <code>{chat_id}</code>\n\n"
            "Укажите его в LEADS_CHAT_ID, чтобы заявки приходили именно сюда.\n"
            "В группе пишите команду как /id@AnaKwork_bot.",
        )
        return

    if command == "/cancel":
        LEADS.pop(chat_id, None)
        send_message(chat_id, "Хорошо, заявка отменена. Выберите раздел в меню.", reply_markup=main_keyboard())
        return

    if text == ORDER_BUTTON:
        LEADS.pop(chat_id, None)
        start_lead(chat_id, sender.get("username"))
        return

    if text in content.SECTIONS:
        LEADS.pop(chat_id, None)
        send_message(chat_id, content.SECTIONS[text], reply_markup=section_keyboard())
        return

    if chat_id in LEADS:
        if text:
            continue_lead(chat_id, text)
        else:
            send_message(chat_id, "Пожалуйста, ответьте текстом — так я смогу передать заявку.")
        return

    send_message(chat_id, content.FALLBACK, reply_markup=main_keyboard())


def handle_callback(callback: dict) -> None:
    chat_id = callback["message"]["chat"]["id"] if callback.get("message") else callback["from"]["id"]
    try:
        call("answerCallbackQuery", callback_query_id=callback["id"])
    except RuntimeError as error:
        print(f"! answerCallbackQuery: {error}", flush=True)

    if callback.get("data") == "order":
        LEADS.pop(chat_id, None)
        start_lead(chat_id, (callback.get("from") or {}).get("username"))


def handle(update: dict) -> None:
    if "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        handle_callback(update["callback_query"])


def poll_once(offset: int, timeout: int = 30) -> int:
    updates = call(
        "getUpdates",
        offset=offset,
        timeout=timeout,
        allowed_updates=ALLOWED_UPDATES,
    )
    for update in updates:
        source = update.get("message") or update.get("callback_query") or {}
        sender = source.get("from") or {}
        preview = (source.get("text") or source.get("data") or "")[:60]
        try:
            handle(update)
            print(
                f"→ {update['update_id']} от {sender.get('id')} @{sender.get('username') or '-'}: {preview!r}",
                flush=True,
            )
        except Exception as error:  # не роняем бота из-за одного апдейта
            print(f"! ошибка обработки {update.get('update_id')}: {error}", flush=True)
        offset = update["update_id"] + 1
        save_offset(offset)
    return offset


def main() -> None:
    load_env_file()
    args = set(sys.argv[1:])
    if "--set-commands" in args:
        set_commands()
        return

    once = "--once" in args
    offset = load_offset()
    print(f"Бот запущен (offset={offset}, режим={'once' if once else 'polling'}).", flush=True)

    empty_polls = 0
    try:
        while True:
            before = offset
            offset = poll_once(offset, timeout=0 if once else 30)
            if once:
                break
            if offset == before:
                empty_polls += 1
                if empty_polls % 10 == 0:
                    print(f"жив, жду сообщения (проверок подряд: {empty_polls})", flush=True)
            else:
                empty_polls = 0
    except KeyboardInterrupt:
        print("\nОстановлен.", flush=True)
    except RuntimeError as error:
        print(f"Ошибка: {error}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
