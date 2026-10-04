import base64
import json
import os

import functions_framework
from google.cloud import billing_v1

PROJECT = f"projects/{os.environ['GOOGLE_CLOUD_PROJECT']}"


@functions_framework.cloud_event
def stop_billing(event):
    """Unlink the project's billing account once a budget message reports cost over budget."""
    budget = json.loads(base64.b64decode(event.data["message"]["data"]))
    print(f"cost {budget['costAmount']} of budget {budget['budgetAmount']}")
    if budget["costAmount"] <= budget["budgetAmount"]:
        return
    billing_v1.CloudBillingClient().update_project_billing_info(
        name=PROJECT, project_billing_info=billing_v1.ProjectBillingInfo(billing_account_name="")
    )
    print(f"billing disabled for {PROJECT}")
