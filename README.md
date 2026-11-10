# SafeCart

SafeCart is a local AI shopping agent that helps a person find products and build a
budgeted basket, while deterministic Python code controls every approval and payment
decision. It addresses a practical problem with agentic commerce: a model or hostile
product listing must not be able to change a budget, approve a purchase, or spend money
by itself.

SafeCart is run locally by following the Quick Start below. **No hosted demo is
provided.** PayPal runs in sandbox mode, so no real money or shipping is involved.

## Quick Start

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/Maiii66/SafeCart_agent-.git SafeCart
cd SafeCart
python -m venv venv
```

Windows PowerShell:

```powershell
venv\Scripts\Activate.ps1
```

Windows Command Prompt:

```bat
venv\Scripts\activate.bat
```

macOS/Linux:

```bash
source venv/bin/activate
```

### 2. Install and configure

```bash
pip install -r requirements.txt
```

Windows:

```bat
copy .env.example .env
```

macOS/Linux:

```bash
cp .env.example .env
```

Open `.env` and set `USE_DEMO_CATALOG=true`. Add your Gemini and PayPal sandbox
credentials. Generate the two local secrets with these commands and paste each result
into `.env`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Use one generated value for `SIGNING_SECRET` and a second generated value for `API_KEY`.
The demo catalog works without a Channel3 key and includes a deliberately hostile
listing. To see it, keep `USE_DEMO_CATALOG=true`, search for `earbuds`, and open the
guardian/risk details or the “How SafeCart decided” panel.

### 3. Start the two local processes

In the activated environment, run these in two terminals from the repository root.

Windows PowerShell or Command Prompt:

```bat
uvicorn backend.main:app --reload
streamlit run frontend/app.py
```

macOS/Linux:

```bash
uvicorn backend.main:app --reload
streamlit run frontend/app.py
```

The API listens on `http://127.0.0.1:8000` and Streamlit normally opens
`http://localhost:8501`. The PayPal return URL in `.env.example` points to the
Streamlit URL.

## Features

- Background shopping agent that proposes products and a basket, with a **“How SafeCart
  decided”** panel showing the trace and safety reasoning.
- AI guardian review that adds risk context to product results; deterministic rule flags
  remain authoritative.
- Plain-English spending rules that are parsed into a proposal and saved only after
  confirmation.
- Voice input through Streamlit’s microphone control; Gemini transcribes the clip and
  SafeCart reads back the parsed goal before searching. Voice cannot approve or pay.
- PayPal Orders v2 sandbox checkout: the buyer approves on PayPal and SafeCart captures
  the order after returning to the local app.
- A built-in fictional demo catalog, including hostile listings for demonstrating the
  injection guard.

## Architecture: the AI proposes, deterministic code decides

Gemini may help understand a request, parse a spending rule, review risk, explain
choices, call the bounded shopping tools, or transcribe voice. Its output is untrusted.
The backend validates it and the payment path does not import the AI module. Channel3
data is converted into SafeCart models before it reaches the safety checks.

Before payment, the deterministic path checks:

1. The budget is a positive integer amount and does not exceed `MAX_BUDGET`.
2. Each offer has a positive price, the configured currency (`USD` by default), `InStock`
   availability, and a `new` condition.
3. The exact offer plus shipping fits the approved budget.
4. Product text passes the injection scan; suspicious listings are flagged or rejected.
5. The person creates a signed, expiring, one-time approval for the exact item, merchant,
   price, total, and budget.
6. The live offer still exists, remains valid, and has the exact approved price.
7. Checkout is sandbox-only, the order is not paused, and the daily and hourly limits
   allow it.
8. A step-up confirmation is required when the configured threshold or risk conditions
   require it.
9. PayPal receives amounts reconstructed from verified integer cents, with an
   idempotency key.

## Tools used

- **Gemini** (`gemini-2.5-flash` by default): request understanding, plain-English
  spending-rule parsing, guardian listing review, bounded agent tool calling, product
  explanations, and voice transcription. Safe rule-based fallbacks are used when Gemini
  is unavailable.
- **Channel3**: live product search and current product details when
  `USE_DEMO_CATALOG=false` and `CHANNEL3_API_KEY` is configured. The demo catalog or
  fallback can be used instead.
- **PayPal Orders v2 sandbox**: creates the checkout order, sends the buyer to the
  sandbox approval page, and captures the approved order on return. SafeCart does not
  use live PayPal mode in this project.

## The 15-scenario attack check

Run:

```bash
python -m scripts.try_attacks
```

This uses a temporary database, a fake PayPal client, and no keys or network. It checks
15 checkout cases, including approval replay, signature tampering, expiry, price changes,
daily and hourly limits, pause, live-mode refusal, step-up confirmation, and an unknown
merchant. It also checks that the hostile demo listing is flagged. The command exits
successfully only when the expected result and PayPal call count match for every case.

## Configuration

Copy `.env.example` to `.env`. Never commit `.env`.

| Variable | Default | Purpose |
|---|---:|---|
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model used by the wrapper |
| `USE_DEMO_CATALOG` | `false` | Use the built-in fictional catalog |
| `CATALOG_FALLBACK` | `true` | Fall back to the demo catalog if Channel3 is unavailable |
| `PAYPAL_ENV` | `sandbox` | PayPal environment; checkout is intended for sandbox |
| `PAYPAL_CURRENCY` | `USD` | Only currency accepted by the guardrails |
| `MAX_BUDGET` | `2000` | Maximum single-request budget, in dollars |
| `STEP_UP_THRESHOLD` | `25` | Amount threshold for typed confirmation, in dollars |
| `DAILY_CAP` | `500` | Daily spending cap, in dollars |
| `MAX_ORDERS_PER_HOUR` | `3` | Maximum orders in one hour |
| `MAX_AUDIO_SECONDS` | `15` | Maximum voice clip duration |
| `APPROVAL_TTL_SECONDS` | `300` | Approval lifetime, in seconds |
| `BACKEND_URL` | `http://127.0.0.1:8000` | URL used by the Streamlit client |
| `DB_PATH` | `data/safecart.db` | SQLite database path |

`GEMINI_API_KEY`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `SIGNING_SECRET`, and
`API_KEY` have no committed defaults and must be supplied for the corresponding
features. `CHANNEL3_API_KEY` is needed only for live Channel3 search.

## PayPal and Gemini setup

Create a PayPal Developer account, create a **sandbox app**, and copy its sandbox
client ID and secret into `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`. In the same
PayPal Developer dashboard, create or use a sandbox **Personal** buyer account and use
that account on the PayPal approval page. Do not use a real PayPal account.

Create a Gemini API key in Google AI Studio and put it in `GEMINI_API_KEY`. Restart both
the backend and Streamlit processes after changing `.env`.

## Tests

Install the requirements, then run:

```bash
python -m pytest -q
python -m scripts.try_attacks
```

The current repository run is **1,130 passed** for `python -m pytest -q`; rerun the
command after changes rather than relying on this recorded count. Tests and the attack
check do not require live Gemini, Channel3, or PayPal credentials.

## Troubleshooting

- **Voice shows an error:** check `GEMINI_API_KEY`, confirm the configured model is
  available, and restart both the backend and Streamlit after editing `.env`.
- **PayPal approval does not confirm:** return to the configured
  `PAYPAL_RETURN_URL` (normally `http://localhost:8501`) and restart both processes
  before trying again.
- **429 rate limit:** wait and try again. Gemini and Channel3 both report rate-limit
  failures without bypassing a safety check.
- **Port already in use:** stop the process using port 8000 or 8501, or change the
  local command/configuration consistently before restarting.
- **Streamlit microphone permission:** allow microphone access for the browser and
  reload the page; typed input remains available if permission is denied.

## Project status

The repository contains the FastAPI backend, Streamlit frontend, demo catalog, Channel3
adapter, Gemini wrapper and PayPal sandbox adapter. The local test suite and the
15-scenario attack check are runnable without external keys. Live Gemini, Channel3,
and PayPal behavior requires the credentials described above. There is no hosted demo
or hosting deployment included.
