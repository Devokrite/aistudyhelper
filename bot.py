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

# Get keys from environment variables (we'll set them in hosting)
TELEGRAM_BOT_TOKEN = os.environ.get("8582279256:AAHCV9tVXyICtjLrpRbHpvhrh5T9t8jVyTk")
OPENAI_API_KEY = os.environ.get("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")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set")

client = OpenAI(api_key=OPENAI_API_KEY)

# In-memory user state
USER_STATE = {}
# USER_STATE[user_id] = {
#   "subj_id": "eco",
#   "ch_id": "eco_ch1",
#   "flashcards": [{"q": "...", "a": "..."}],
#   "index": 0
# }


def extract_text_from_pdf(pdf_path: str) -> str:
    """Read the PDF file from the repo and extract text."""
    reader = PdfReader(pdf_path)
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    full_text = "\n\n".join(parts)
    # To avoid sending huge text to the model, truncate for now:
    return full_text[:15000]


def generate_flashcards(text: str, max_cards: int = 20):
    """
    Ask OpenAI to generate flashcards from a chapter text.
    Returns a list of {"q": "...", "a": "..."}.
    """
    prompt = f"""
