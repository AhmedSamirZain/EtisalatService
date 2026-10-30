# Etisalat Mix Telegram Bot (hosted with Streamlit)

This project runs an admin-controlled Telegram bot from a Streamlit-hosted process. **Only two values belong in Streamlit Secrets:** `TELEGRAM_BOT_TOKEN` and `ADMIN_ID`. Owner/child emails and passwords are entered in the private Telegram chat for each run, used only in memory, and are not saved to Secrets or Git.

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

When Telegram polling starts, the bot sends the primary admin a startup message and shows the **👥 لوحة الإدارة** button. The admin must open the bot and send `/start` once first, because Telegram does not allow bots to initiate a private conversation. From `/admin`, the primary admin can manage authorized users: `/adduser 123456789`, `/grant 123456789 10`, `/removeuser 123456789`, `/users`, and `/userstats 123456789`. Use `/settings` or **⚙️ إعدادات التغيير والحالة** to configure plan retry timing, retry count, and live status refresh. New users start with zero activations; each confirmed run start consumes one activation, even if it later fails or is stopped. The report separately counts attempts, successful runs (target reached), failures, and stopped runs. The primary admin has unlimited runs. Each authorized person uses their own account credentials in a private chat; only one paid run can be active at a time. Only the admin and the user who started the active run can view its detailed status or stop it. The allowlist and activation records are stored in an ignored local SQLite file (`.streamlit/authorized_users.sqlite3`), not in Secrets or Git. If the hosting filesystem is reset during redeployment, user access and activation records may need to be restored.

After `/start`, use the persistent buttons **تشغيل**, **الحالة**, **إيقاف**, **إلغاء**, **مساعدة**, and **البداية**. The run button asks for a target, shows a safety confirmation, collects the owner/child credentials for that run, and asks for a final confirmation. Text commands are also supported: `/run 50000`, `/status`, `/stop`, `/cancel`, and `/help`; administrators can use `/settings` to change runtime behavior. Targets are limited to 1–1,000,000 mixes.

On `/stop`, the bot finishes the in-flight request, starts no further renewals or plan changes, reads the currently available child balance with at most three quick attempts, and sends one final child-to-owner transfer if the balance is positive. It does not wait for Etisalat to credit 800/1,300 mixes back to the child: if the readable balance is zero, it stops immediately. If a transfer response is ambiguous, it checks once and never blindly repeats it. Network outages can still prevent confirming or completing the cleanup; in that case the bot reports the uncertainty and stops safely.

All status reads pass through a shared retry gate, and an unreadable status is never treated as a zero balance or proof that the family member is missing. The bot only continues after an exact plan ID is confirmed. If a fresh successful status read still shows the previous plan after the configured interval, it can automatically retry the plan-change request; status timeouts or unexpected plan IDs never trigger a blind retry. The admin controls the retry interval, retry limit (including unlimited), and live Telegram status refresh interval from `/settings` or the admin panel. Defaults are 4 minutes, 3 retries, and a 5-second status refresh. The live status edits one existing Telegram message with current/target plans, retry count, countdown, and recent progress. These settings are stored in the ignored local SQLite database and apply during an active run. If the retry limit is reached, the bot keeps monitoring and updating status but does not send more change requests. `/stop` remains available throughout.

Only private messages from the primary admin (`ADMIN_ID`) or IDs added by that admin are authorized. Group chats are not allowed. Do not run another polling instance with the same token.

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
