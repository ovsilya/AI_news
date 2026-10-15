#!/usr/bin/env bash
# Deploys AI_news to GCP: one image, three Cloud Run jobs, Cloud Scheduler triggers, and a private bucket
# that holds the database and your private files between runs (src/ainews/cloud.py).
#
#   infra/deploy.sh setup      APIs, image repository, state bucket, service accounts, the bot-token secret
#   infra/deploy.sh state      upload data/ainews.sqlite and private/ (the first time; refuses to replace)
#   infra/deploy.sh release    build the image and create or update the three jobs (after every code change)
#   infra/deploy.sh schedule   create or update the Scheduler triggers: from then on it posts on its own
#   infra/deploy.sh unschedule pause all triggers (the jobs and data stay)
#
# Each step can be run again safely. Run from the repo folder, signed in to gcloud as the project owner.
set -euo pipefail
cd "$(dirname "$0")/.."

from_env() {   # a non-secret value from .env, without printing it
  grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true
}

PROJECT="${GCP_PROJECT_ID:-$(from_env GCP_PROJECT_ID)}"
[[ -n "$PROJECT" ]] || { echo "Set GCP_PROJECT_ID in .env" >&2; exit 1; }
REGION="${AINEWS_REGION:-us-west1}"
TZ_NAME="${AINEWS_TZ:-$(from_env AINEWS_TZ)}"   # the schedules' time zone, e.g. Europe/London; UTC if unset
TZ_NAME="${TZ_NAME:-UTC}"
BUCKET="${PROJECT}-ainews-state"
REPO="ainews"
RUN_SA="ainews-run@${PROJECT}.iam.gserviceaccount.com"
SCHED_SA="ainews-scheduler@${PROJECT}.iam.gserviceaccount.com"
SECRET="ainews-telegram-bot-token"
G=(--project "$PROJECT" --quiet)

# Lane jobs: name, command, timeout. An evening brief would be one more: run,--edition,pm,--send
JOBS=(
  "ainews-jobs|jobs,--send-openings|45m"
  "ainews-brief|run,--send|45m"
  "ainews-weekly|weekly,--send|20m"
)
# Jobs + openings and the brief run every EVERY_DAYS days (1 for daily). Cron counts days of the month, so
# 3 means the 3rd, 6th, ... 30th: never two days running, and a longer gap (4-5 days) after a month's last run.
EVERY_DAYS=3
DAYS="$EVERY_DAYS-31/$EVERY_DAYS"
# Triggers: name, job, cron, in TZ_NAME.
SCHEDULES=(
  "ainews-jobs-am|ainews-jobs|0 5 $DAYS * *"
  "ainews-brief-am|ainews-brief|0 6 $DAYS * *"
  "ainews-weekly-mon|ainews-weekly|0 7 * * 1"
)
RETIRED=(ainews-jobs-daily)   # earlier trigger names; schedule removes them so they can't keep firing

say() { printf '\n== %s\n' "$*"; }

setup() {
  say "Enabling APIs"
  gcloud services enable run.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com \
    artifactregistry.googleapis.com cloudbuild.googleapis.com storage.googleapis.com aiplatform.googleapis.com "${G[@]}"

  say "Image repository $REPO ($REGION), keeping the 3 newest images"
  gcloud artifacts repositories describe "$REPO" --location "$REGION" "${G[@]}" >/dev/null 2>&1 ||
    gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION" \
      --description "AI_news images" "${G[@]}"
  gcloud artifacts repositories set-cleanup-policies "$REPO" --location "$REGION" \
    --policy infra/image-cleanup.json --no-dry-run "${G[@]}" >/dev/null

  say "State bucket gs://$BUCKET: private, versioned (20 earlier copies, at most 30 days)"
  gcloud storage buckets describe "gs://$BUCKET" "${G[@]}" >/dev/null 2>&1 ||
    gcloud storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access \
      --public-access-prevention "${G[@]}"
  gcloud storage buckets update "gs://$BUCKET" --versioning --lifecycle-file infra/state-lifecycle.json "${G[@]}"

  say "Service accounts: $RUN_SA runs the jobs, $SCHED_SA starts them"
  for sa in ainews-run ainews-scheduler; do
    gcloud iam service-accounts describe "$sa@$PROJECT.iam.gserviceaccount.com" "${G[@]}" >/dev/null 2>&1 ||
      gcloud iam service-accounts create "$sa" --display-name "AI_news ${sa#ainews-}" "${G[@]}"
  done
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$RUN_SA" --role roles/aiplatform.user \
    --condition None "${G[@]}" >/dev/null
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$RUN_SA" \
    --role roles/storage.objectAdmin "${G[@]}" >/dev/null

  say "Secret $SECRET (the Telegram bot token)"
  gcloud secrets describe "$SECRET" "${G[@]}" >/dev/null 2>&1 ||
    gcloud secrets create "$SECRET" --replication-policy automatic "${G[@]}"
  gcloud secrets add-iam-policy-binding "$SECRET" --member "serviceAccount:$RUN_SA" \
    --role roles/secretmanager.secretAccessor "${G[@]}" >/dev/null
  if [[ -z "$(gcloud secrets versions list "$SECRET" --filter state:enabled --limit 1 --format 'value(name)' "${G[@]}")" ]]; then
    cat <<EOF

The secret has no value yet. Add your bot token from .env yourself (it isn't printed):

  grep '^TELEGRAM_BOT_TOKEN=' .env | cut -d= -f2- | tr -d "\n\"'" | gcloud secrets versions add $SECRET --data-file=- --project $PROJECT
EOF
  fi
}

state() {
  say "Uploading the database and private files to gs://$BUCKET"
  .venv/bin/ainews state push --private --bucket "$BUCKET"
}

release() {
  local chat tag image env
  chat="$(from_env TELEGRAM_CHAT_ID)"
  [[ -n "$chat" ]] || { echo "TELEGRAM_CHAT_ID is missing from .env" >&2; exit 1; }
  tag="$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- src config prompts pyproject.toml Dockerfile || echo -dirty)"
  image="$REGION-docker.pkg.dev/$PROJECT/$REPO/ainews:$tag"

  say "Building $image"
  gcloud builds submit --tag "$image" "${G[@]}" .

  env="^@^GCP_PROJECT_ID=$PROJECT@VERTEX_REGION=global@AINEWS_LLM_ENABLED=true@AINEWS_STATE_BUCKET=$BUCKET@TZ=$TZ_NAME"
  env+="@TELEGRAM_CHAT_ID=$chat"
  env+="@AINEWS_BRIEF_LOOKBACK_HOURS=$((24 * (EVERY_DAYS + 2) + 2))"   # since the last brief, month-end gaps too
  for key in AINEWS_MAX_USD_PER_RUN AINEWS_MAX_USD_PER_DAY AINEWS_MAX_USD_PER_MONTH AINEWS_MAX_JOBS_EXTRACT \
             AINEWS_BRIEF_RESERVE_USD AINEWS_VOICE AINEWS_VOICE_NAME MODEL_VOICE AINEWS_VOICE_MAX_USD_PER_BRIEF \
             AINEWS_VOICE_MAX_USD_PER_MONTH; do
    value="$(from_env "$key")"
    if [[ -n "$value" ]]; then env+="@$key=$value"; fi
  done

  for spec in "${JOBS[@]}"; do
    IFS='|' read -r name args timeout <<<"$spec"
    say "Job $name: ainews ${args//,/ }"
    gcloud run jobs deploy "$name" --image "$image" --region "$REGION" --service-account "$RUN_SA" \
      --tasks 1 --max-retries 0 --task-timeout "$timeout" --cpu 1 --memory 2Gi \
      --set-env-vars "$env" --set-secrets "TELEGRAM_BOT_TOKEN=$SECRET:latest" --args="$args" "${G[@]}" >/dev/null
    gcloud run jobs add-iam-policy-binding "$name" --region "$REGION" --member "serviceAccount:$SCHED_SA" \
      --role roles/run.invoker "${G[@]}" >/dev/null
  done
  echo
  echo "Run one by hand: gcloud run jobs execute ainews-brief --region $REGION --project $PROJECT --wait"
}

schedule() {
  for spec in "${SCHEDULES[@]}"; do
    IFS='|' read -r name job cron <<<"$spec"
    say "Trigger $name: $job at '$cron' ($TZ_NAME)"
    verb=create
    gcloud scheduler jobs describe "$name" --location "$REGION" "${G[@]}" >/dev/null 2>&1 && verb=update
    gcloud scheduler jobs "$verb" http "$name" --location "$REGION" --schedule "$cron" --time-zone "$TZ_NAME" \
      --uri "https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/$job:run" --http-method POST \
      --oauth-service-account-email "$SCHED_SA" "${G[@]}" >/dev/null
    gcloud scheduler jobs resume "$name" --location "$REGION" "${G[@]}" >/dev/null 2>&1 || true
  done
  for name in "${RETIRED[@]}"; do
    if gcloud scheduler jobs describe "$name" --location "$REGION" "${G[@]}" >/dev/null 2>&1; then
      say "Removing the retired trigger $name"
      gcloud scheduler jobs delete "$name" --location "$REGION" "${G[@]}"
    fi
  done
}

unschedule() {
  for spec in "${SCHEDULES[@]}"; do
    IFS='|' read -r name _ _ <<<"$spec"
    say "Pausing $name"
    gcloud scheduler jobs pause "$name" --location "$REGION" "${G[@]}"
  done
}

case "${1:-}" in
  setup|state|release|schedule|unschedule) "$1" ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
