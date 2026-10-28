# Etisalat Mix Bot — Streamlit control panel

This repository contains a Streamlit control panel (`app.py`) for the existing Etisalat automation script (`etisalat mix.py`). The bot logs in to the owner and child accounts, then can renew a bundle, migrate plans, and transfer mixes. These are real account operations and may incur charges.

## Local run

1. Install Python 3.9 or newer.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and replace the sample values with your real values. Never commit the real secrets file.
4. Start the app:
   ```bash
   streamlit run app.py --server.address 0.0.0.0
   ```

## Streamlit Community Cloud

1. Push this repository to GitHub.
2. In Streamlit Community Cloud, create an app from this repository and choose `app.py` as the main file.
3. In the app's **Settings → Secrets**, add the values shown in `.streamlit/secrets.toml.example`. Do not add real credentials to GitHub.
4. Restrict app access using the hosting platform's access controls. The app also has a shared dashboard-password gate (`APP_ACCESS_PASSWORD`), but that alone is not a substitute for platform access controls.

The app uses one background thread inside the Streamlit process. It is not a durable worker: if the hosted app sleeps, restarts, or is redeployed, the automation stops. Keep the app running and watch its status/log panel.

## Secret keys

- `APP_ACCESS_PASSWORD`: shared password for the dashboard.
- `OWNER_EMAIL`, `OWNER_PASSWORD`: Etisalat owner account credentials.
- `CHILD_EMAIL`, `CHILD_PASSWORD`: Etisalat child account credentials.

The current Python script is not a Telegram bot and does not read a Telegram `BOT_TOKEN` or `ADMIN_ID`; those values are intentionally not included or used. Add them only if a Telegram integration is implemented.
