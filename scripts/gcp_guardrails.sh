#!/usr/bin/env bash
# GCP guardrails for the AI_news project. Review before running; it changes billing settings.
#
#   scripts/gcp_guardrails.sh PROJECT_ID BILLING_ACCOUNT_ID [MONTHLY_AMOUNT] [--with-kill-switch]
#
# MONTHLY_AMOUNT is in the billing account's own currency (budgets must use it): e.g. 14 for a CAD
# account is about US$10.
#
# 1. Creates a monthly budget scoped to this project, with email alerts to billing admins at
#    25 / 50 / 90 / 100 % of actual spend and at 100 % of forecasted spend.
# 2. With --with-kill-switch: also publishes budget updates to Pub/Sub and deploys the
#    infra/billing-guard function, which unlinks billing once actual spend exceeds the budget.
#
# Budget alerts are informational and arrive with a delay (billing data lags by hours), so the
# app's own caps (AINEWS_MAX_USD_*) remain the first line of defence.
set -euo pipefail

PROJECT_ID="${1:?project id}"
BILLING_ACCOUNT="${2:?billing account id, e.g. 012345-6789AB-CDEF01 (gcloud billing accounts list)}"
AMOUNT="${3:-10}"
KILL_SWITCH="${4:-}"
REGION=us-central1
TOPIC=billing-guard

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable billingbudgets.googleapis.com cloudbilling.googleapis.com
CURRENCY=$(gcloud billing accounts describe "$BILLING_ACCOUNT" --format="value(currencyCode)")
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

BUDGET_ARGS=(
  --billing-account="$BILLING_ACCOUNT"
  --display-name="AI_news monthly cap"
  --budget-amount="${AMOUNT}${CURRENCY}"
  --filter-projects="projects/$PROJECT_NUMBER"
  --threshold-rule=percent=0.25
  --threshold-rule=percent=0.5
  --threshold-rule=percent=0.9
  --threshold-rule=percent=1.0
  --threshold-rule=percent=1.0,basis=forecasted-spend
)

if [[ "$KILL_SWITCH" == "--with-kill-switch" ]]; then
  gcloud services enable pubsub.googleapis.com cloudfunctions.googleapis.com run.googleapis.com \
    cloudbuild.googleapis.com eventarc.googleapis.com artifactregistry.googleapis.com
  gcloud pubsub topics describe "$TOPIC" >/dev/null 2>&1 || gcloud pubsub topics create "$TOPIC"
  BUDGET_ARGS+=(--notifications-rule-pubsub-topic="projects/$PROJECT_ID/topics/$TOPIC")

  SA="billing-guard@$PROJECT_ID.iam.gserviceaccount.com"
  gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
    gcloud iam service-accounts create billing-guard --display-name="AI_news billing kill switch"
  # Project Billing Manager on this project only: enough to unlink billing, nothing else.
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" \
    --role=roles/billing.projectManager --condition=None >/dev/null

  gcloud functions deploy billing-guard --gen2 --runtime=python312 --region="$REGION" \
    --source=infra/billing-guard --entry-point=stop_billing --trigger-topic="$TOPIC" \
    --service-account="$SA" --set-env-vars="GCP_PROJECT_ID=$PROJECT_ID,DRY_RUN=false" \
    --memory=256Mi --max-instances=1
fi

gcloud billing budgets create "${BUDGET_ARGS[@]}"
echo "Budget of $AMOUNT $CURRENCY/month created for $PROJECT_ID${KILL_SWITCH:+ (kill switch on)}."
