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
2. **Models.** The defaults are Google's own models, which need no access request: **Gemini 3.1 Flash-Lite**, the cheapest current model, judges stories, and **Gemini 3.5 Flash-Lite** writes the brief, because its explanations are noticeably better for a small extra cost. For even better writing, set `MODEL_EDITOR=gemini-3.8-flash`. Both are already available once the Vertex AI API is enabled; you can see them under Vertex AI → Model Garden. Claude can be switched on later with `LLM_PROVIDER=anthropic`, but Anthropic models need an access request in Model Garden first.
3. **Local credentials.** Sign in with your **personal** Google account:
   ```bash
   gcloud auth application-default login
   ```
   ```bash
   gcloud auth application-default set-quota-project ai-news-XXXX
   ```
4. **Configure.** In `.env`, set `GCP_PROJECT_ID=ai-news-XXXX`. It still holds the placeholder `your-personal-project-id` until you change it.
5. **Check.** This makes one tiny call per model (under $0.01 in total) and works while the switch is off. It also confirms the model IDs match Model Garden.
   ```bash
   .venv/bin/ainews check
   ```

## 3. Spending guardrails

Layers 1 and 2 are already in the code. Layer 3 needs nothing from you while you use Gemini. Layers 4 and 5 are set up in GCP by you.

| # | Guardrail | What it stops | Acts | Where |
|---|---|---|---|---|
| 1 | **The button**: `AINEWS_LLM_ENABLED=false` (the default) | Every model call, except the deliberate `ainews check` | Immediately | code ✓ |
| 2 | **App caps**: $0.30 per run, $0.75 per day, $10 per month; at most 120 stories judged; editor output capped at 32k tokens | Any single call whose *worst-case* cost would cross a cap. It is refused before it is sent, and the brief falls back to the digest without AI | Before each call | code ✓ |
| 3 | **Vertex AI quotas**: none to lower for Gemini (see below) | — | — | — |
| 4 | **Budget alerts**: about US$10/month (in your billing currency), emails at 25/50/90/100% and at 100% forecast | Nothing by itself; it tells you | Hours of delay | script |
| 5 | **Kill switch** (optional): unlinks billing when actual spend passes the budget | Everything paid in the project | Hours of delay | script |

**Check spend and caps at any time:**
```bash
.venv/bin/ainews budget
```

**Layer 3, quotas.** Gemini on Vertex AI has no per-project limit you can lower. It runs on Google's shared capacity (*Standard PayGo*): your organisation gets a guaranteed share of throughput based on its last 30 days of spend, and anything above that is served when spare capacity exists. A 429 error means Google is busy, not that you hit a limit. So the only real-time cap is layer 2, inside the app. Calls made outside the app, for example with leaked credentials, are stopped only by keeping the credentials safe and by the alerts in layers 4 and 5. Claude models on Vertex do have per-model quotas (IAM & Admin → **Quotas & system limits**), so lower those if you switch to `LLM_PROVIDER=anthropic`.

**Layers 4 and 5, budget and kill switch.** Read [scripts/gcp_guardrails.sh](../scripts/gcp_guardrails.sh) first. It changes billing settings. The amount is in your billing account's currency, because Google only accepts budgets in it: for a CAD account use about 14 for US$10. Check the currency with `gcloud billing accounts describe BILLING_ACCOUNT_ID --format="value(currencyCode)"`.

Budget alerts only:
```bash
scripts/gcp_guardrails.sh ai-news-XXXX BILLING_ACCOUNT_ID 10
```

Budget plus kill switch, which deploys the small [billing-guard](../infra/billing-guard/main.py) function:
```bash
scripts/gcp_guardrails.sh ai-news-XXXX BILLING_ACCOUNT_ID 10 --with-kill-switch
```

The kill switch unlinks billing from the project once *actual* spend passes the budget. That stops Vertex AI and everything else in the project until you relink billing in the console, which is another reason to use a dedicated project. Google's billing data lags by several hours, so layers 1 and 2 are the real-time protection, and 4–5 are the backstop.

**Credentials.**
- There is no Anthropic API key anywhere. The app signs in to Vertex AI with your Google login on this laptop.
- After deployment it will use a dedicated service account with only the *Vertex AI User* role.
- `.env` is gitignored. On Cloud Run the Telegram token will live in Secret Manager.

**Expected cost once on:** about $0.03–0.05 per brief: the judge on Gemini 3.1 Flash-Lite ($0.25 / $1.50 per 1M tokens) costs about a cent for the new stories, and the editor on 3.5 Flash-Lite ($0.30 / $2.50) about two to three cents. With two briefs a day that's about $2–3 a month. A run after changing `prompts/judge.md` re-scores every candidate and costs about $0.05. Prices are from the Vertex AI pricing page, checked 2026-10-12.

## 4. Turning the button on

1. `ainews check` passes for both models and Telegram.
2. `ainews tg-test` reaches the channel.
3. Budget alerts are created (plus the kill switch if you want it).
4. In `.env`, set `AINEWS_LLM_ENABLED=true`.
5. Do a first run without posting, then read the brief in `out/` and check the cost:
   ```bash
   .venv/bin/ainews run
   ```
   ```bash
   .venv/bin/ainews budget
   ```
6. When it looks right, post that same brief to the channel:
   ```bash
   .venv/bin/ainews send
   ```
   To rebuild the latest brief instead (for example after editing `prompts/editor.md`), run `ainews run --redo`. Its stories become available again and the judge's scores are reused, so only the editor is paid for again.
7. To clear test posts out of the channel, keeping only the latest sent brief:
   ```bash
   .venv/bin/ainews tg-clean
   ```
   It asks before deleting. Telegram lets a bot delete its own posts only within 48 hours; it lists anything it couldn't remove (service messages, posts you wrote yourself, older posts), so you can delete those by hand.

**To switch off again,** set `AINEWS_LLM_ENABLED=false`. It takes effect on the next run.

## 5. Jobs lane and the weekly jobs report

Nothing to sign up for: the employers' job boards (Greenhouse, Lever, Ashby) are public APIs, and the Workday career sites of banks, pharma and big tech answer the same JSON requests their own pages send. How each step works, with the settings and what to do when something looks wrong: [How the job search works](jobs/index.html) and [Run and change it](jobs/operate.html).

1. **Your skills.** `private/skills.yaml` lists the skills you `have` and are `learning`; the report's "Your fit" section compares them with what postings ask for. It was drafted from `private/profile.md`, so check it. Names follow [config/skills.yaml](../config/skills.yaml) (aliases such as `k8s` work).
2. **Daily, after the brief:**
   ```bash
   .venv/bin/ainews jobs
   ```
   It polls the 97 job boards (about 80 seconds) and the 32 Workday career sites (about 4 minutes; the first runs take longer while they read each new posting once) in [config/companies.yaml](../config/companies.yaml), records new, changed and closed postings, and has Gemini 3.1 Flash-Lite read up to 200 new ones (about $0.001 each, so about $0.22 a run). The first runs work through a backlog of about 2,400 postings, about 2,100 still unread on 2026-10-13, so it takes about ten daily runs to read them all. To go faster, run it more than once a day: each run stays within the daily cap.
3. **Daily, your openings:**
   ```bash
   .venv/bin/ainews openings
   ```
   It ranks the week's new postings by fit to your skills and level (free, no model call), prints them with their fit, and writes `out/openings-<date>.md`. Post them with `ainews send --openings`, or build and post in one go with `ainews openings --send`; each posting is sent once. Your preferences (role families, markets, levels, years, how many, the fit bar) go in `private/openings.yaml`: copy [config/openings.example.yaml](../config/openings.example.yaml). [Openings for you](jobs/openings.html) explains the score.
4. **Weekly, on Mondays:**
   ```bash
   .venv/bin/ainews weekly
   ```
   It writes `out/jobs-<date>.md` (about 1 cent for the notes). Read it, then post it with `ainews send --report`, or build and post in one go with `ainews weekly --send`. Rising and falling skills appear after two weeks of tracking.
5. **Add an employer:** `ainews jobs-probe <slug> …` prints a ready-to-paste line for `config/companies.yaml` when the employer uses Greenhouse, Lever or Ashby. For a Workday employer, `ainews jobs-probe --workday <tenant> …` (the first part of its `*.myworkdayjobs.com` address, e.g. `nvidia`) lists the career sites its robots.txt allows, with their open postings; keep the main one and drop the sites that repeat it.
