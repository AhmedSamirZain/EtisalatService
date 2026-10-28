# Etisalat Mix Telegram Bot (hosted with Streamlit)

This project exposes the existing Etisalat mix automation through an **admin-only Telegram bot** and a Streamlit status/control page. Telegram commands are `/start`, `/help`, `/status`, `/run <mix_target>`, and `/stop`. `/run` asks for an inline confirmation before it starts.

The automation can renew bundles, migrate plans, and transfer mixes. These are real account operations and may incur charges. Verify the account and cost before confirming a run.

## Deploy on Streamlit Community Cloud

1. Create a Telegram bot with **@BotFather** and copy its token. Keep the token private.
2. Find your numeric Telegram user ID (the administrator who is allowed to control this bot). Use your personal user ID, not a group/chat ID.
3. Install/deploy this GitHub repository in Streamlit Community Cloud. Set the main file to `app.py`.
4. In the app's **Settings → Secrets**, add the keys from `.streamlit/secrets.toml.example`, replacing every example with your values. Required keys:
   - `APP_ACCESS_PASSWORD`: password for the Streamlit dashboard.
   - `OWNER_EMAIL`, `OWNER_PASSWORD`: Etisalat owner account.
   - `CHILD_EMAIL`, `CHILD_PASSWORD`: Etisalat child account.
   - `TELEGRAM_BOT_TOKEN`: token from @BotFather.
   - `ADMIN_ID`: numeric Telegram user ID authorized to issue commands.
5. Deploy/reboot the app. The Telegram polling worker starts when the Streamlit app process starts. Open the bot in Telegram, send `/start`, then `/help`.

Never commit `.streamlit/secrets.toml` or paste real tokens/passwords into GitHub or chat. The example file is safe to commit; it contains placeholders only.

## Commands

- `/start` or `/help` — instructions.
- `/status` — automation status and recent log lines.
- `/run 50000` — request an automation run for that target; confirm via the button in Telegram. The accepted target is 1 to 1,000,000 mixes.
- `/stop` — request a stop after the current API call finishes.

Only private Telegram messages from the account whose numeric user ID matches `ADMIN_ID` are authorized. Keep the bot token private and do not run another polling instance with the same token, or Telegram may reject duplicate polling.

## Local run

```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit .streamlit/secrets.toml with your actual secrets, then:
streamlit run app.py
```

## Hosting limitation

Telegram uses long polling in a background thread inside the Streamlit process. It runs only while the hosted app process is awake. Streamlit Community Cloud may sleep/restart an app, which will stop the Telegram bot and any active automation; check the Streamlit dashboard and bot status after restarts. For guaranteed 24/7 Telegram availability, use an always-on worker host instead of relying on a sleeping Streamlit instance.
