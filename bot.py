import os
import time
import threading
import requests
import telebot

# ── Config ──────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]
AGENT_BUILDER   = os.environ.get("AGENT_BUILDER_ID", "")
AGENT_QA        = os.environ.get("AGENT_QA_ID", "")
AGENT_UX        = os.environ.get("AGENT_UX_ID", os.environ.get("UX_AGENT_ID", ""))
ENV_ID          = os.environ.get("ENVIRONMENT_ID", os.environ.get("UX_ENV_ID", ""))
OWNER_ID        = os.environ.get("TELEGRAM_OWNER_ID", "")

BASE = "https://api.anthropic.com/v1"
HEADERS = {
    "Content-Type": "application/json",
    "x-api-key": ANTHROPIC_KEY,
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "managed-agents-2026-04-01",
}

AGENTS = {
    "build": AGENT_BUILDER,
    "qa": AGENT_QA,
    "ux": AGENT_UX,
}

CLONE = "git clone https://github.com/Rezimod/Stellar.git /home/user/stellar 2>/dev/null; "

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ── Agent API ────────────────────────────────────────

def create_session(agent_id, title="TG session"):
    r = requests.post(f"{BASE}/sessions", headers=HEADERS, json={
        "agent": agent_id,
        "environment_id": ENV_ID,
        "title": title[:80],
    })
    data = r.json()
    if "id" not in data:
        raise Exception(f"Session failed: {data}")
    return data["id"]


def send_message(session_id, text):
    """Send user message — correct format for Managed Agents API"""
    r = requests.post(f"{BASE}/sessions/{session_id}/events", headers=HEADERS, json={
        "events": [{
            "type": "user.message",
            "content": [{"type": "text", "text": text}]
        }]
    })
    if r.status_code >= 400:
        raise Exception(f"Send failed ({r.status_code}): {r.text[:200]}")
    return r.json()


def poll_response(session_id, timeout_s=480):
    """Poll until idle/completed, then fetch agent text from events endpoint"""
    polls = timeout_s // 8
    for _ in range(polls):
        time.sleep(8)
        r = requests.get(f"{BASE}/sessions/{session_id}", headers=HEADERS)
        status = r.json().get("status", "")

        if status in ("idle", "completed"):
            return fetch_agent_text(session_id)
        if status == "failed":
            return "❌ Agent failed. Check Console → Sessions."

    return None  # timeout


def fetch_agent_text(session_id):
    """Fetch the last agent messages from the events endpoint"""
    r = requests.get(
        f"{BASE}/sessions/{session_id}/events?limit=15&order=desc",
        headers=HEADERS,
    )
    if r.status_code >= 400:
        return "(could not fetch events)"

    events = r.json().get("data", [])
    texts = []
    for ev in events:
        if ev.get("type") == "agent.message":
            for block in ev.get("content", []):
                if block.get("type") == "text":
                    texts.append(block["text"])

    texts.reverse()  # chronological
    return "\n\n".join(texts) if texts else "(no text output)"


# ── Helpers ──────────────────────────────────────────

def send_long(chat_id, text, parse_mode=None):
    """Split messages for Telegram's 4096 char limit"""
    for i in range(0, len(text), 3900):
        chunk = text[i:i+3900]
        try:
            bot.send_message(chat_id, chunk, parse_mode=parse_mode)
        except Exception:
            # fallback without parse_mode if markdown breaks
            bot.send_message(chat_id, chunk)


def truncate(text, limit=3500):
    if len(text) <= limit:
        return text
    half = limit // 2 - 40
    return text[:half] + "\n\n... [TRUNCATED — see Console] ...\n\n" + text[-half:]


def run_in_background(chat_id, agent_key, task, label):
    """Run agent in a thread so Telegram doesn't block"""
    agent_id = AGENTS.get(agent_key)
    if not agent_id:
        bot.send_message(chat_id, f"❌ Agent '{agent_key}' not configured. Add {agent_key.upper()}_AGENT_ID env var.")
        return

    emoji = {"build": "🔨", "qa": "🧪", "ux": "🎨"}.get(agent_key, "🤖")
    name = {"build": "Builder", "qa": "QA", "ux": "UX Advisor"}.get(agent_key, agent_key)

    try:
        sid = create_session(agent_id, f"TG: {label[:60]}")
        bot.send_message(chat_id,
            f"📡 Session: `{sid}`\n"
            f"⏳ {emoji} {name} working... (up to 8 min)\n\n"
            f"Console: https://console.anthropic.com/sessions/{sid}",
            parse_mode="Markdown"
        )

        send_message(sid, task)
        result = poll_response(sid)

        if result is None:
            bot.send_message(chat_id,
                f"⏰ Still running after 8 min.\n"
                f"Check Console: https://console.anthropic.com/sessions/{sid}"
            )
            return

        # Count issues for QA reports
        summary_lines = []
        crits = result.count("[CRITICAL]")
        warns = result.count("[WARN]")
        infos = result.count("[INFO]")
        prompts = result.lower().count("claude code")

        if crits + warns + infos > 0:
            summary_lines.append("📊 *Issues:*")
            if crits: summary_lines.append(f"  🔴 {crits} CRITICAL")
            if warns: summary_lines.append(f"  🟡 {warns} WARN")
            if infos: summary_lines.append(f"  🔵 {infos} INFO")

        if prompts:
            summary_lines.append(f"📋 {prompts} Claude Code prompts ready")

        summary = "\n".join(summary_lines)
        header = f"{emoji} *{name} — Done*\n\n{summary}\n\n---\n\n" if summary else f"{emoji} *{name} — Done*\n\n"

        send_long(chat_id, header + truncate(result), parse_mode="Markdown")
        bot.send_message(chat_id,
            f"📄 Full report: https://console.anthropic.com/sessions/{sid}",
        )

    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {str(e)[:500]}")


def bg(chat_id, agent_key, task, label):
    """Start background thread"""
    t = threading.Thread(target=run_in_background, args=(chat_id, agent_key, task, label))
    t.daemon = True
    t.start()


# ── Prompt Templates ─────────────────────────────────

PROMPTS = {
    "audit": CLONE + (
        "Audit the entire Stellar app. Map all pages (ls src/app/), API routes (ls src/app/api/), "
        "components, and libs. For each: check TypeScript errors, missing error handling, security issues, "
        "crypto jargon in UI. Output a numbered list sorted CRITICAL > WARN > INFO. "
        "For each CRITICAL issue, include a Claude Code fix prompt."
    ),
    "flows": CLONE + (
        "Test every user flow: 1) Registration/login via Privy 2) Mission start → camera → verify → mint → success "
        "3) Quiz completion → reward 4) NFT gallery loading 5) Profile + Stars balance 6) Marketplace. "
        "For each: list every state transition, find missing states, output Claude Code fix prompts."
    ),
    "ux": CLONE + (
        "Review every page as a first-time Georgian user with zero crypto knowledge. "
        "For each page rate SHIP IT / NEEDS WORK / RETHINK. Find all crypto jargon "
        "(grep for NFT, wallet, blockchain, Solana, token, mint in user-facing text). "
        "Count taps to core actions. Output Claude Code prompts for all fixes with English + Georgian copy."
    ),
    "security": CLONE + (
        "Security audit: grep for hardcoded keys in src/, check .gitignore, verify API routes handle errors "
        "without leaking secrets, check auth gates, verify devnet only, check rate limiting on rewards, "
        "find exploit vectors (can user farm infinite Stars?). Output Claude Code fix prompts."
    ),
}


# ── Commands ─────────────────────────────────────────

@bot.message_handler(commands=["start", "help"])
def help_cmd(message):
    bot.reply_to(message, (
        "🔭 *Stellar Agent Controller*\n\n"
        "*Quick commands:*\n"
        "/audit — Full QA audit\n"
        "/flows — Test all user flows\n"
        "/ux — Complete UX review\n"
        "/security — Security + exploit audit\n\n"
        "*Agent commands:*\n"
        "/build <task> — Builder analyzes + outputs prompts\n"
        "/qa <task> — QA tests specific area\n"
        "/uxr <task> — UX reviews specific flow\n"
        "/ask <build|qa|ux> <task> — Any agent\n\n"
        "*Examples:*\n"
        "`/build analyze observe-to-earn and output Claude Code prompts`\n"
        "`/qa test what happens when mint fails`\n"
        "`/uxr review the mission completion screen`\n"
        "`/security check if Stars rewards can be exploited`\n\n"
        "Short summary here. Full report in Console link."
    ), parse_mode="Markdown")


@bot.message_handler(commands=["status"])
def status_cmd(message):
    try:
        # Test with whichever agent is configured
        agent_id = AGENT_UX or AGENT_QA or AGENT_BUILDER
        if not agent_id:
            bot.reply_to(message, "❌ No agents configured.")
            return
        sid = create_session(agent_id, "status check")
        alive = []
        if AGENT_BUILDER: alive.append("🔨 Builder")
        if AGENT_QA: alive.append("🧪 QA")
        if AGENT_UX: alive.append("🎨 UX")
        bot.reply_to(message, f"✅ Agents alive: {', '.join(alive)}\nTest session: `{sid}`", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)[:300]}")


# ── Quick templates ──────────────────────────────────

@bot.message_handler(commands=["audit"])
def audit_cmd(message):
    bot.reply_to(message, "🧪 Starting full audit...")
    bg(message.chat.id, "qa", PROMPTS["audit"], "Full audit")

@bot.message_handler(commands=["flows"])
def flows_cmd(message):
    bot.reply_to(message, "🧪 Testing all user flows...")
    bg(message.chat.id, "qa", PROMPTS["flows"], "Flow tests")

@bot.message_handler(commands=["ux"])
def ux_cmd(message):
    bot.reply_to(message, "🎨 Running UX review...")
    bg(message.chat.id, "ux", PROMPTS["ux"], "UX review")

@bot.message_handler(commands=["security"])
def security_cmd(message):
    bot.reply_to(message, "🔒 Running security audit...")
    bg(message.chat.id, "qa", PROMPTS["security"], "Security audit")


# ── Agent-specific commands ──────────────────────────

@bot.message_handler(commands=["build"])
def build_cmd(message):
    task = message.text[7:].strip()
    if not task:
        bot.reply_to(message, "Usage: /build <describe what to build>")
        return
    bot.reply_to(message, f"🔨 Builder starting: {task[:100]}")
    bg(message.chat.id, "build", CLONE + task, task[:60])

@bot.message_handler(commands=["qa"])
def qa_cmd(message):
    task = message.text[4:].strip()
    if not task:
        bot.reply_to(message, "🧪 Starting default full audit...")
        bg(message.chat.id, "qa", PROMPTS["audit"], "Full audit")
        return
    bot.reply_to(message, f"🧪 QA starting: {task[:100]}")
    bg(message.chat.id, "qa", CLONE + task, task[:60])

@bot.message_handler(commands=["uxr"])
def uxr_cmd(message):
    task = message.text[5:].strip()
    if not task:
        bot.reply_to(message, "🎨 Starting default UX review...")
        bg(message.chat.id, "ux", PROMPTS["ux"], "UX review")
        return
    bot.reply_to(message, f"🎨 UX reviewing: {task[:100]}")
    bg(message.chat.id, "ux", CLONE + task, task[:60])

@bot.message_handler(commands=["ask"])
def ask_cmd(message):
    parts = message.text[5:].strip().split(" ", 1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /ask <build|qa|ux> <task>")
        return
    agent_key, task = parts[0].lower(), parts[1]
    if agent_key not in AGENTS:
        bot.reply_to(message, f"Unknown agent: {agent_key}. Use build, qa, or ux.")
        return
    emoji = {"build": "🔨", "qa": "🧪", "ux": "🎨"}[agent_key]
    bot.reply_to(message, f"{emoji} Starting: {task[:100]}")
    bg(message.chat.id, agent_key, CLONE + task, task[:60])


# ── Start ────────────────────────────────────────────
print("✅ Stellar bot polling...")
bot.infinity_polling()
