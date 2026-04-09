import os
import time
import threading
import requests
import telebot

# ── Config ──────────────────────────────────────────
TELEGRAM_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
AGENT_ID          = os.environ["UX_AGENT_ID"]
ENV_ID            = os.environ["UX_ENV_ID"]

HEADERS = {
    "Content-Type": "application/json",
    "x-api-key": ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "managed-agents-2026-04-01"
}

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ── Core Functions ───────────────────────────────────

def create_session():
    r = requests.post(
        "https://api.anthropic.com/v1/sessions",
        headers=HEADERS,
        json={
            "environment_id": ENV_ID,
            "agent": {"type": "agent", "id": AGENT_ID}
        }
    )
    data = r.json()
    if "id" not in data:
        raise Exception(f"Session creation failed: {data}")
    return data["id"]

def extract_text_from_session(session_data):
    """
    Robustly extract agent output from session response.
    Tries multiple event structures the API might return.
    """
    events = session_data.get("events", [])

    # Try 1: look for 'agent' type events with text (what Console UI shows)
    for event in reversed(events):
        if event.get("type") == "agent" and event.get("content"):
            content = event["content"]
            if isinstance(content, str) and len(content) > 20:
                return content
            if isinstance(content, list):
                texts = [b.get("text","") for b in content if b.get("type")=="text"]
                combined = "\n".join(texts).strip()
                if combined:
                    return combined

    # Try 2: look for 'message' type
    for event in reversed(events):
        if event.get("type") == "message":
            content = event.get("content", "")
            if isinstance(content, str) and len(content) > 20:
                return content

    # Try 3: look for any event with a 'text' field > 100 chars (agent output)
    for event in reversed(events):
        text = event.get("text", "")
        if isinstance(text, str) and len(text) > 100:
            return text

    # Try 4: look for 'assistant' type (original attempt)
    for event in reversed(events):
        if event.get("role") == "assistant" or event.get("type") == "assistant":
            content = event.get("content") or event.get("text", "")
            if isinstance(content, str) and len(content) > 20:
                return content
            if isinstance(content, list):
                texts = [b.get("text","") for b in content if b.get("type")=="text"]
                return "\n".join(texts).strip()

    # Debug fallback: show raw event types so we can fix
    types = [e.get("type","?") for e in events[-5:]]
    return f"⚠️ Could not extract output. Last event types: {types}\nCheck Console → Sessions for full result."

def run_agent(prompt, timeout_seconds=300):
    session_id = create_session()

    requests.post(
        f"https://api.anthropic.com/v1/sessions/{session_id}/events",
        headers=HEADERS,
        json={"events": [{"type": "user", "text": prompt}]}
    )

    polls = timeout_seconds // 8  # check every 8 seconds
    for i in range(polls):
        time.sleep(8)
        r = requests.get(
            f"https://api.anthropic.com/v1/sessions/{session_id}",
            headers=HEADERS
        )
        data = r.json()
        status = data.get("status", "unknown")

        if status == "completed":
            return extract_text_from_session(data)
        elif status in ("failed", "error", "cancelled"):
            error = data.get("error", "No details")
            return f"❌ Agent {status}: {error}"
        # still running — continue polling

    return f"⏱ Timeout after {timeout_seconds}s. Session: `{session_id}`\nCheck Console → Sessions for output."

def send_long(chat_id, text):
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        bot.send_message(chat_id, chunk)

# ── Commands ─────────────────────────────────────────

@bot.message_handler(commands=["start", "help"])
def help_cmd(message):
    bot.reply_to(message, (
        "🌌 *Stellarr Agent Commander*\n\n"
        "/audit — Full UX audit (~3 min)\n"
        "/quick — Quick homepage check (~2 min)\n"
        "/status — Test agent connection\n"
        "/debug — Show raw session data\n"
        "/help — This message"
    ), parse_mode="Markdown")

@bot.message_handler(commands=["status"])
def status_cmd(message):
    try:
        sid = create_session()
        bot.reply_to(message, f"✅ Agent alive\n`{sid}`", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ {str(e)}")

@bot.message_handler(commands=["debug"])
def debug_cmd(message):
    """Shows raw session response — use this to fix event parsing"""
    bot.reply_to(message, "🔍 Creating debug session...")
    try:
        sid = create_session()
        requests.post(
            f"https://api.anthropic.com/v1/sessions/{sid}/events",
            headers=HEADERS,
            json={"events": [{"type": "user", "text": "Say hello in exactly 5 words."}]}
        )
        time.sleep(30)
        r = requests.get(f"https://api.anthropic.com/v1/sessions/{sid}", headers=HEADERS)
        data = r.json()
        status = data.get("status")
        events = data.get("events", [])
        summary = f"Status: {status}\nEvents ({len(events)}):\n"
        for e in events[-6:]:
            etype = e.get("type","?")
            content = str(e.get("content") or e.get("text",""))[:80]
            summary += f"  [{etype}] {content}\n"
        bot.send_message(message.chat.id, f"```\n{summary}\n```", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ {str(e)}")

@bot.message_handler(commands=["audit"])
def audit_cmd(message):
    bot.reply_to(message, "🔭 Full UX audit running...\n⏳ ~3 minutes. Sit tight.")
    def run():
        try:
            result = run_agent(
                "Run a full UX audit of stellarrclub.vercel.app. "
                "Check all pages: homepage, /sky, /chat, /marketplace, /missions, /profile. "
                "Return structured scorecard: Critical fixes / Important / Working Well / Score out of 10."
            , timeout_seconds=360)
            send_long(message.chat.id, result)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error: {str(e)}")
    threading.Thread(target=run).start()

@bot.message_handler(commands=["quick"])
def quick_cmd(message):
    bot.reply_to(message, "⚡ Quick check running...\n⏳ ~2 minutes.")
    def run():
        try:
            result = run_agent(
                "Visit stellarrclub.vercel.app homepage only. "
                "Give 5 bullet UX observations and the single most urgent fix. Max 150 words."
            , timeout_seconds=240)
            send_long(message.chat.id, result)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error: {str(e)}")
    threading.Thread(target=run).start()

# ── Start ─────────────────────────────────────────────
print("✅ Stellarr bot polling...")
bot.infinity_polling()
