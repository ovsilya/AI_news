"""Billing kill switch: unlinks billing from the project when actual spend passes the budget.

Triggered by Cloud Billing budget notifications on a Pub/Sub topic (Google's "disable billing with
notifications" pattern). Once billing is unlinked, every paid service in the project stops,
including Vertex AI. Relink billing in the console to resume.

Set DRY_RUN=true to only log what it would do.
"""
import base64
import json
import os

import functions_framework
from google.cloud import billing_v1

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
PROJECT_NAME = f"projects/{PROJECT_ID}"
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"


@functions_framework.cloud_event
def stop_billing(cloud_event):
    data = json.loads(base64.b64decode(cloud_event.data["message"]["data"]).decode("utf-8"))
    cost, budget = float(data["costAmount"]), float(data["budgetAmount"])
    if cost <= budget:
        print(f"{PROJECT_ID}: cost {cost:.2f} <= budget {budget:.2f}, nothing to do")
        return

    client = billing_v1.CloudBillingClient()
    info = client.get_project_billing_info(name=PROJECT_NAME)
    if not info.billing_enabled:
        print(f"{PROJECT_ID}: billing already disabled")
        return
    if DRY_RUN:
        print(f"{PROJECT_ID}: DRY_RUN, would disable billing (cost {cost:.2f} > budget {budget:.2f})")
        return
    client.update_project_billing_info(
        name=PROJECT_NAME, project_billing_info=billing_v1.ProjectBillingInfo(billing_account_name="")
    )
    print(f"{PROJECT_ID}: billing DISABLED (cost {cost:.2f} > budget {budget:.2f})")
