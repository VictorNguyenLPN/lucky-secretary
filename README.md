<div align="center">

<h3>Lucky Secretary</h3>

<p>Personal AI secretary on Telegram</p>

<img src="assets/lucky-secretary-logo.jpg" alt="Lucky Secretary" width="320" />

<br/>

[![GitHub Stars](https://img.shields.io/github/stars/VictorNguyenLPN/lucky-secretary?style=flat-square&logo=github)](https://github.com/VictorNguyenLPN/lucky-secretary/stargazers) [![GitHub Forks](https://img.shields.io/github/forks/VictorNguyenLPN/lucky-secretary?style=flat-square&logo=github)](https://github.com/VictorNguyenLPN/lucky-secretary/network/members) [![GitHub Issues](https://img.shields.io/github/issues/VictorNguyenLPN/lucky-secretary?style=flat-square&logo=github)](https://github.com/VictorNguyenLPN/lucky-secretary/issues)

</div>

## Features

- Bilingual natural language extraction (Vietnamese & English) with relative date/time resolution.
- Multi-turn clarification workflow when title, date, or time is missing or ambiguous.
- Explicit event preview with **Confirm** / **Cancel** inline keyboard buttons.
- Persistent SQLite state (`users`, `conversations`, `calendar_requests`) surviving restarts.
- Atomic duplicate-confirmation protection and configurable confirmation expiration (default 15 minutes).
- Whitelist-restricted Telegram access (`TELEGRAM_ALLOWED_USER_IDS`).
- Provider-agnostic LLM core via OpenAI-compatible SDK (supports **Google Gemini Free Tier**, **Groq Free Tier**, and **OpenAI**).

## Architecture

Modular Monolith under `src/lucky_secretary/`:

- `bot/`: Telegram Long Polling handlers (`/start`, `/help`, `/cancel`, text messages, inline callbacks).
- `core/`: Config validation, prompt templates, LLM provider abstraction, and `CalendarAgent`.
- `services/`: Conversation state machine and calendar orchestration.
- `integrations/google_calendar/`: Google OAuth 2.0 credential refresh and Calendar API v3 `events.insert`.
- `database/`: `aiosqlite` schema initialization and atomic repository transitions.

---

## Setup Guide

### 1. Create a Telegram Bot & Find Your User ID

1. Open Telegram and message `@BotFather`.
2. Send `/newbot`, choose a name and username, and copy the **Bot Token** (`TELEGRAM_BOT_TOKEN`).
3. Message `@userinfobot` to get your numeric Telegram User ID (`TELEGRAM_ALLOWED_USER_IDS`).

### 2. Get an LLM API Key (Free Tier Recommended)

**Option A - Google Gemini Free Tier (Default):**

1. Visit [Google AI Studio](https://aistudio.google.com/apikey) and create an API key.
2. Set in `.env`:
   ```env
   OPENAI_API_KEY=<your-gemini-api-key>
   OPENAI_MODEL=gemini-3.5-flash-lite
   OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
   ```

**Option B - OpenAI:**

```env
OPENAI_API_KEY=<your-openai-api-key>
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
```

### 3. Setup Google Cloud OAuth 2.0 (Google Calendar)

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g. `Lucky Secretary`).
3. Enable **Google Calendar API** under **APIs & Services > Library**.
4. Configure **OAuth consent screen**:
   - User Type: **External** (keep in _Testing_ mode for personal use).
   - Add your Google email under **Test users**.
   - Add scope: `https://www.googleapis.com/auth/calendar.events`.
5. Go to **Credentials > Create Credentials > OAuth client ID**:
   - Application type: **Desktop app**.
   - Copy **Client ID** (`GOOGLE_CLIENT_ID`) and **Client Secret** (`GOOGLE_CLIENT_SECRET`).
6. Copy `.env.example` to `.env` and fill in all values:
   ```bash
   cp .env.example .env
   ```
7. Run the OAuth setup helper once to authorize your account and generate `./data/google_token.json`:
   ```bash
   uv run python scripts/setup_google_oauth.py
   ```
   _(Or pass `--client-secret-file path/to/client_secret.json` if you downloaded the JSON file.)_

---

## Running Locally

```bash
uv sync
uv run python -m lucky_secretary.main
```

## Running Tests

```bash
uv run pytest -v
```

## Running with Docker

Ensure `.env` and `./data/google_token.json` exist locally first, then:

```bash
docker compose up -d --build
docker compose logs -f
```

SQLite data (`./data/lucky_secretary.db`) and OAuth credentials (`./data/google_token.json`) are persisted in `./data`.

---

## Usage Examples

- **Direct creation (Vietnamese):**
  - User: `Tạo lịch họp với team vào 9 giờ sáng mai.`
  - Bot: Displays `Calendar Event Preview` (`09:00 - 10:00`, `Asia/Ho_Chi_Minh`) with **Confirm** / **Cancel** buttons.
  - User clicks **Confirm** -> Bot creates the event and returns the Google Calendar link.
- **Clarification workflow:**
  - User: `Tạo lịch họp với team vào thứ 6.`
  - Bot: `Bạn muốn họp vào lúc mấy giờ?`
  - User: `9 giờ sáng.`
  - Bot: Displays `Calendar Event Preview` with **Confirm** / **Cancel** buttons.
- **English with duration:**
  - User: `Schedule a meeting tomorrow at 2 PM for 90 minutes.`
  - Bot: Displays preview for `14:00 - 15:30` and waits for confirmation.
