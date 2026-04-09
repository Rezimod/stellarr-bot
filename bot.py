import os
import time
import requests
import telebot

# ── Config ──────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

AGENT_ID = os.environ["UX_AGENT_ID"]   # your full agent ID
ENV_ID   = os.environ["UX_ENV_ID"]     # your environment ID

HEADERS = {
    "Content-Type": "application/json",
    "x-api-key": ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "managed-agents-2026-04-01"
}

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ── Core Agent Functions ─────────────────────────────

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

def run_agent(prompt, timeout_seconds=180):
    session_id = create_session()

    # Send trigger message
    requests.post(
        f"https://api.anthropic.com/v1/sessions/{session_id}/events",
        headers=HEADERS,
        json={"events": [{"type": "user", "text": prompt}]}
    )

    # Poll until done
    polls = timeout_seconds // 5
    for _ in range(polls):
        time.sleep(5)
        r = requests.get(
            f"https://api.anthropic.com/v1/sessions/{session_id}",
            headers=HEADERS
        ).json()

        status = r.get("status")
        if status == "completed":
            for event in reversed(r.get("events", [])):
                if event.get("type") == "assistant":
                    return event.get("text", "Agent returned no text.")
            return "Completed but no assistant message found."
        elif status == "failed":
            return "❌ Agent failed. Check Console → Sessions for details."

    return "⏱ Timeout — agent is still running. Check Console manually."

def send_long(chat_id, text):
    """Split messages > 4000 chars for Telegram limit"""
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        bot.send_message(chat_id, chunk)

# ── Commands ─────────────────────────────────────────

@bot.message_handler(commands=["start", "help"])
def help_cmd(message):
    bot.reply_to(message, (
        "🌌 *Stellarr Agent Commander*\n\n"
        "/audit — Full UX audit of stellarrclub.vercel.app\n"
        "/quick — Fast UX check (shorter, 60 sec)\n"
        "/status — Is the agent alive?\n"
        "/help — This message"
    ), parse_mode="Markdown")

@bot.message_handler(commands=["status"])
def status_cmd(message):
    try:
        sid = create_session()
        bot.reply_to(message, f"✅ Agent is alive.\nSession created: `{sid}`", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Agent error: {str(e)}")

@bot.message_handler(commands=["audit"])
def audit_cmd(message):
    bot.reply_to(message, "🔭 Running full UX audit...\n⏳ Takes ~2 minutes. I'll send results when done.")
    try:
        result = run_agent(
            "Run a full UX audit of stellarrclub.vercel.app right now. "
            "Navigate all pages and give me the structured scorecard with Critical / Important / Working Well sections."
        )
        send_long(message.chat.id, result)
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=["quick"])
def quick_cmd(message):
    bot.reply_to(message, "⚡ Running quick check...\n⏳ ~60 seconds.")
    try:
        result = run_agent(
            "Visit stellarrclub.vercel.app homepage only. "
            "Give me a 5-bullet UX assessment and one single most urgent fix. Max 200 words."
        , timeout_seconds=90)
        send_long(message.chat.id, result)
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# ── Start ────────────────────────────────────────────
print("✅ Stellarr bot polling...")
bot.infinity_polling()