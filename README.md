# Etisalat Mix Telegram Bot (hosted with Streamlit)

This project runs an admin-only Telegram bot from a Streamlit-hosted process. **Only two values belong in Streamlit Secrets:** `TELEGRAM_BOT_TOKEN` and `ADMIN_ID`. Owner/child emails and passwords are entered in the private Telegram chat for each run, used only in memory, and are not saved to Secrets or Git.

The automation can renew bundles, migrate plans, and transfer mixes. These are real account operations and may incur charges. Verify the account and cost before confirming a run.

## Streamlit Community Cloud deployment

1. Create a Telegram bot with **@BotFather** and copy its token. Keep it private.
2. Get your numeric personal Telegram user ID (for example, from `@userinfobot`). Use the personal ID, not a group/chat ID.
3. Deploy this GitHub repository in Streamlit Community Cloud with `app.py` as the main file.
4. In **Settings → Secrets**, add only:
   ```toml
   TELEGRAM_BOT_TOKEN = "paste-your-BotFather-token"
   ADMIN_ID = 123456789
   ```
5. Deploy or reboot the app. Open your bot in Telegram, send `/start`, then use `/help`.

Do not put account emails/passwords or the bot token in GitHub. The repository ignores `.streamlit/secrets.toml`; the example file contains placeholders only.

## Telegram buttons and commands

After `/start`, use the persistent buttons **تشغيل**, **الحالة**, **إيقاف**, **إلغاء**, **مساعدة**, and **البداية**. The run button asks for a target, shows a safety confirmation, collects the owner/child credentials for that run, and asks for a final confirmation. Text commands are also supported: `/run 50000`, `/status`, `/stop`, `/cancel`, and `/help`. Targets are limited to 1–1,000,000 mixes.

All status reads now pass through one shared retry gate used by the initial check, plan confirmation, cycle checks, and balance checks before transfers. It starts with quick requests, then backs off (250ms, 750ms, and 2 seconds) with longer server timeouts. A read timeout is never treated as a zero balance or proof that the family member is missing; the bot waits and reports progress periodically, and `/stop` remains available. The member must appear in status or be absent from three successful reads before the bot stops. A previously confirmed member number is reused if a later response omits it. After a plan-change request, the bot waits for an exact match of the requested plan ID before continuing and never resends that state-changing request just because status is delayed. The bot does not proceed with renewals, transfers, or later plan changes based on unreadable status. Polling cannot make an unresponsive Etisalat endpoint respond faster.

Only private messages from the Telegram account whose numeric user ID matches `ADMIN_ID` are authorized. Do not run another polling instance with the same token.

### Credential privacy

Telegram account credentials are not written to Streamlit Secrets, Git, or the bot's logs. They are held temporarily in process memory for the current run and the bot attempts to delete each email/password message after reading it; the conversation state is cleared after confirmation, cancellation, or a 10-minute timeout. **Deletion is best effort, not a guarantee:** Telegram handles the messages, so only enter credentials if you accept that. Use a private chat, never a group.

## Local run

```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit that file with only TELEGRAM_BOT_TOKEN and ADMIN_ID, then:
streamlit run app.py
```

## Hosting limitation

Telegram long polling and the automation run in background threads inside the Streamlit process. If the hosted app sleeps, restarts, or is redeployed, the Telegram bot and active run stop. For reliable 24/7 availability, use an always-on worker host rather than a sleeping Streamlit instance.
