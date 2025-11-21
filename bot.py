import os
import json
from pypdf import PdfReader
from openai import OpenAI
from subjects_config import SUBJECTS

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# === CONFIG: read keys from environment variables ===
TELEGRAM_BOT_TOKEN = os.environ.get("8582279256:AAHCV9tVXyICtjLrpRbHpvhrh5T9t8jVyTk")
OPENAI_API_KEY = os.environ.get("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")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment variables.")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set in environment variables.")

client = OpenAI(api_key=OPENAI_API_KEY)

# In-memory user state
# USER_STATE[user_id] = {
#   "subj_id": "eco",
#   "ch_id": "eco_l1",
#   "flashcards": [{"q": "...", "a": "..."}],
#   "index": 0,
# }
USER_STATE: dict[int, dict] = {}


# ---------- Helpers ----------

def extract_text_from_pdf(pdf_path: str) -> str:
    """Read the PDF file from the repo and extract text."""
    reader = PdfReader(pdf_path)
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    full_text = "\n\n".join(parts)

    # To avoid sending a *huge* amount of text to the model, truncate for now
    return full_text[:15000]


def generate_flashcards(text: str, max_cards: int = 20) -> list[dict]:
    """
    Ask OpenAI to generate flashcards from a chapter text.
    Returns a list of {"q": "...", "a": "..."}.
    """

    prompt = f"""
You are helping a first-year university student study.

From the chapter text below, create up to {max_cards} flashcards.
Focus on the most important terms, definitions, key ideas, and formulas.

Return ONLY valid JSON in this exact format (no extra text):

[
  {{"q": "Question text here", "a": "Answer text here"}},
  ...
]

CHAPTER TEXT:
\"\"\"{text}\"\"\"
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",  # you can change model if needed
        messages=[
            {"role": "system", "content": "You create clear study flashcards."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    content = response.choices[0].message.content

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        print("⚠️ Failed to parse JSON from model. Raw content:")
        print(content)
        return []

    flashcards: list[dict] = []
    for item in data:
        q = item.get("q") or item.get("question")
        a = item.get("a") or item.get("answer")
        if q and a:
            flashcards.append({"q": q.strip(), "a": a.strip()})

    return flashcards


# ---------- Handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command: let user choose a subject."""
    if not update.message:
        return

    user_id = update.effective_user.id
    USER_STATE.pop(user_id, None)  # reset state

    keyboard: list[list[InlineKeyboardButton]] = []
    for subj_id, subj_info in SUBJECTS.items():
        keyboard.append([
            InlineKeyboardButton(
                text=subj_info["name"],
                callback_data=f"SUBJ|{subj_id}",
            )
        ])

    await update.message.reply_text(
        "👋 Hi! I'm your AI study bot.\nChoose a subject:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline button presses."""
    query = update.callback_query
    if not query:
        return

    await query.answer()
    data = query.data.split("|")
    action = data[0]
    user_id = query.from_user.id

    # ---------- Subject chosen ----------
    if action == "SUBJ":
        subj_id = data[1]
        subj_info = SUBJECTS[subj_id]

        USER_STATE[user_id] = {"subj_id": subj_id}

        chapters = subj_info["chapters"]
        keyboard: list[list[InlineKeyboardButton]] = []
        for ch_id, ch_info in chapters.items():
            keyboard.append([
                InlineKeyboardButton(
                    text=ch_info["name"],
                    callback_data=f"CH|{subj_id}|{ch_id}",
                )
            ])

        await query.edit_message_text(
            text=f"📚 Subject: *{subj_info['name']}*\nChoose a chapter:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    # ---------- Chapter chosen ----------
    elif action == "CH":
        subj_id = data[1]
        ch_id = data[2]

        subj_info = SUBJECTS[subj_id]
        ch_info = subj_info["chapters"][ch_id]
        pdf_path = ch_info["pdf"]

        try:
            text = extract_text_from_pdf(pdf_path)
        except FileNotFoundError:
            await query.edit_message_text(
                text=f"😕 I couldn't find the file for this chapter:\n`{pdf_path}`",
                parse_mode="Markdown",
            )
            return

        if not text.strip():
            await query.edit_message_text(
                text="😕 The PDF seems to be empty or not readable as text."
            )
            return

        flashcards = generate_flashcards(text, max_cards=15)

        USER_STATE[user_id] = {
            "subj_id": subj_id,
            "ch_id": ch_id,
            "flashcards": flashcards,
            "index": 0,
        }

        if not flashcards:
            await query.edit_message_text(
                text="😕 I couldn't generate flashcards for this chapter. "
                     "Maybe the PDF is mostly images or formatted strangely."
            )
            return

        await send_flashcard_question(query, user_id)

    # ---------- Show answer ----------
    elif action == "SHOW":
        subj_id, ch_id, index_str = data[1], data[2], data[3]
        index = int(index_str)

        state = USER_STATE.get(user_id)
        if not state:
            await query.edit_message_text("Session expired. Use /start again.")
            return

        flashcards = state["flashcards"]
        if index >= len(flashcards):
            await query.edit_message_text("No more cards. Use /start for another chapter.")
            return

        card = flashcards[index]

        if index < len(flashcards) - 1:
            keyboard = [[
                InlineKeyboardButton(
                    text="➡️ Next card",
                    callback_data=f"NEXT|{index + 1}",
                )
            ]]
        else:
            keyboard = [[
                InlineKeyboardButton(
                    text="✅ Finish",
                    callback_data="FINISH",
                )
            ]]

        await query.edit_message_text(
            text=f"❓ *Q*: {card['q']}\n\n✅ *A*: {card['a']}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

    # ---------- Next card ----------
    elif action == "NEXT":
        index = int(data[1])
        state = USER_STATE.get(user_id)
        if not state:
            await query.edit_message_text("Session expired. Use /start again.")
            return

        state["index"] = index
        await send_flashcard_question(query, user_id)

    # ---------- Finish ----------
    elif action == "FINISH":
        USER_STATE.pop(user_id, None)
        await query.edit_message_text(
            text="🎉 You finished this set of flashcards. Use /start to choose another chapter."
        )


async def send_flashcard_question(query, user_id: int):
    """Send only the question side of the current flashcard."""
    state = USER_STATE.get(user_id)
    if not state:
        await query.edit_message_text("Session expired. Use /start again.")
        return

    subj_id = state["subj_id"]
    ch_id = state["ch_id"]
    index = state["index"]
    flashcards = state["flashcards"]

    if not flashcards:
        await query.edit_message_text("No flashcards generated.")
        return
    if index >= len(flashcards):
        await query.edit_message_text("No more cards.")
        return

    subj_name = SUBJECTS[subj_id]["name"]
    ch_name = SUBJECTS[subj_id]["chapters"][ch_id]["name"]
    card = flashcards[index]

    keyboard = [[
        InlineKeyboardButton(
            text="👀 Show answer",
            callback_data=f"SHOW|{subj_id}|{ch_id}|{index}",
        )
    ]]

    await query.edit_message_text(
        text=(
            f"📚 *{subj_name}* – *{ch_name}*\n\n"
            f"Card {index + 1}/{len(flashcards)}\n\n"
            f"❓ *Q*: {card['q']}"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# ---------- Entry point ----------

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_button))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
