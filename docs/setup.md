# Setup: Telegram, GCP and spending guardrails

Work through the sections in order. Model calls stay **off** until the last step.

## 1. Telegram bot and channel (about 10 minutes)

1. **Create the bot.** In Telegram, open **@BotFather** (it has the blue verified check) and send `/newbot`.
   - Display name: anything, for example `AI News Brief`.
   - Username: must end in `bot`, for example `ovsilya_ainews_bot`.

   BotFather replies with a **token** like `123456789:AAH…`. Treat it like a password: anyone who has it can post as your bot.
2. **Lock the bot down.** In BotFather:
   - `/setjoingroups` → your bot → **Disable**, so nobody can add it to other groups.
   - Optionally `/setdescription` and `/setuserpic`.
3. **Create the channel.** In Telegram: New Channel → name it (for example `AI Brief`) → choose **Private**. Only people with the invite link can join, which is what you want for a personal brief.
4. **Make the bot an admin.** Channel → Manage channel → Administrators → Add admin → search for the bot's username.
   - Turn on **Post messages**, plus **Edit messages of others** (later briefs update their own posts).
   - Turn off everything else.
5. **Save the token.** Open `.env` in your editor (copy it from `.env.example` if it doesn't exist) and set `TELEGRAM_BOT_TOKEN=…`. Type it into the file yourself; don't paste it into a chat.
6. **Find the channel's chat ID.** Post any message in the channel (for example "hello"), then run:
   ```bash
   .venv/bin/ainews tg-chats
   ```
   It prints a line like `-1001234567890  channel  AI Brief`. Put that number in `.env` as `TELEGRAM_CHAT_ID`.
7. **Test.**
   ```bash
   .venv/bin/ainews tg-test
   ```
   The channel should receive "AI_news test".

**`tg-chats` says "No chats seen yet":** the bot only receives messages posted *after* it became an admin.
- Check that the bot is listed under the channel's Administrators, then post a new message and rerun.
- If Telegram won't let you add the bot, temporarily send `/setjoingroups` → **Enable** to BotFather, add the bot, then disable it again.
- Another way to get the ID: open the channel in Telegram Web (web.telegram.org/a). The address bar ends with the chat ID (`#-100…`).

If the token ever leaks, send `/revoke` to BotFather, pick the bot, and put the new token in `.env`.

*Later (Phase 2):* the bot gets a webhook for the 👍/👎 buttons and profile edits. After that `tg-chats` stops working, which is expected: Telegram delivers updates either to a webhook or to `getUpdates`, never both.

## 2. GCP project and Vertex AI

1. **Use a dedicated project** for AI_news. Billing, budgets and the optional kill switch then affect nothing else. Replace `ai-news-XXXX` with an unused ID:
   ```bash
   gcloud projects create ai-news-XXXX --name="AI news"
   ```
   ```bash
   gcloud billing accounts list
   ```
   ```bash
   gcloud billing projects link ai-news-XXXX --billing-account=BILLING_ACCOUNT_ID
   ```
   ```bash
   gcloud services enable aiplatform.googleapis.com --project=ai-news-XXXX
   ```
2. **Enable the Claude models.** In the Cloud console go to Vertex AI → **Model Garden**. Search for **Claude Haiku 5.5** → Enable, and accept Anthropic's terms. Do the same for **Claude Opus 5.5**. Enable nothing else; add Claude Sonnet 5.5 only if you want a cheaper editor.
3. **Local credentials.** Sign in with your **personal** Google account:
   ```bash
   gcloud auth application-default login
   ```
   ```bash
   gcloud auth application-default set-quota-project ai-news-XXXX
   ```
4. **Configure.** In `.env`, set `GCP_PROJECT_ID=ai-news-XXXX`.
5. **Check.** This makes one tiny call per model (under $0.01 in total) and works while the switch is off. It also confirms the model IDs match Model Garden.
   ```bash
   .venv/bin/ainews check
   ```

## 3. Spending guardrails

Layers 1 and 2 are already in the code. Layers 3–5 are set up in GCP by you.

| # | Guardrail | What it stops | Acts | Where |
|---|---|---|---|---|
| 1 | **The button**: `AINEWS_LLM_ENABLED=false` (the default) | Every model call, except the deliberate `ainews check` | Immediately | code ✓ |
| 2 | **App caps**: $0.75 per run, $1.50 per day, $20 per month; at most 120 stories judged; editor output capped at 16k tokens | Any single call whose *worst-case* cost would cross a cap. It is refused before it is sent, and the brief falls back to the digest without AI | Before each call | code ✓ |
| 3 | **Vertex AI quotas** lowered per Claude model | Fast burn from a bug, a loop, or someone using your credentials outside the app | Immediately | console |
| 4 | **Budget alerts**: $10/month, emails at 25/50/90/100% and at 100% forecast | Nothing by itself; it tells you | Hours of delay | script |
| 5 | **Kill switch** (optional): unlinks billing when actual spend passes the budget | Everything paid in the project | Hours of delay | script |

**Check spend and caps at any time:**
```bash
.venv/bin/ainews budget
```

**Layer 3, quotas.** Cloud console → IAM & Admin → **Quotas & system limits**. Filter by service *Vertex AI API* and type the model name (for example `claude-opus`). For the requests-per-minute and tokens-per-minute rows of the `global` region, choose **Edit quota** and lower them. A sensible start: Opus 5 requests and 20k input tokens per minute; Haiku 20 requests and 100k input tokens per minute. A brief needs far less than that. Quota decreases are normally applied straight away.

**Layers 4 and 5, budget and kill switch.** Read [scripts/gcp_guardrails.sh](../scripts/gcp_guardrails.sh) first. It changes billing settings.

Budget alerts only:
```bash
scripts/gcp_guardrails.sh ai-news-XXXX BILLING_ACCOUNT_ID 10
```

Budget plus kill switch, which deploys the small [billing-guard](../infra/billing-guard/main.py) function:
```bash
scripts/gcp_guardrails.sh ai-news-XXXX BILLING_ACCOUNT_ID 10 --with-kill-switch
```

The kill switch unlinks billing from the project once *actual* spend passes the budget. That stops Vertex AI and everything else in the project until you relink billing in the console, which is another reason to use a dedicated project. Google's billing data lags by several hours, so layers 1–3 are the real-time protection, and 4–5 are the backstop.

**Credentials.**
- There is no Anthropic API key anywhere. The app signs in to Vertex AI with your Google login on this laptop.
- After deployment it will use a dedicated service account with only the *Vertex AI User* role.
- `.env` is gitignored. On Cloud Run the Telegram token will live in Secret Manager.

**Expected cost once on:** roughly $0.30 per brief, most of it the Opus editor. With two briefs a day that's about $15–25 a month (see the cost estimate in [architecture.md](architecture.md)). For the first week you can set `MODEL_EDITOR=claude-sonnet-5-5` or lower `AINEWS_MAX_USD_PER_DAY`.

## 4. Turning the button on

1. `ainews check` passes for both models and Telegram.
2. `ainews tg-test` reaches the channel.
3. Budget alerts are created (plus the kill switch if you want it), and the Vertex quotas are lowered.
4. In `.env`, set `AINEWS_LLM_ENABLED=true`.
5. Do a first run without posting, then read the brief in `out/` and check the cost:
   ```bash
   .venv/bin/ainews run
   ```
   ```bash
   .venv/bin/ainews budget
   ```
6. When it looks right, post to the channel:
   ```bash
   .venv/bin/ainews run --send
   ```

**To switch off again,** set `AINEWS_LLM_ENABLED=false`. It takes effect on the next run.
