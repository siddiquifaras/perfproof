"""Send a category-heavy GraphQL query to Saleor 20 times in process.

Uses Django's test client against the API view, with the database the
task's setup migrated and filled with ``populatedb``. The query walks
categories, their parents and children and their products, the
resolvers the reference change optimizes. Prints the number of
categories returned so both trees can be compared for equal output.
"""

import json
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "saleor.settings")
django.setup()

from django.test import Client  # noqa: E402

QUERY = """
query {
  categories(first: 50) {
    totalCount
    edges {
      node {
        id
        name
        level
        parent { id name }
        children(first: 20) {
          totalCount
          edges { node { id name parent { id name } } }
        }
        products(first: 10, channel: "default-channel") {
          totalCount
          edges { node { id name } }
        }
      }
    }
  }
}
"""

client = Client()
count = 0
for _ in range(20):
    response = client.post(
        "/graphql/",
        data=json.dumps({"query": QUERY}),
        content_type="application/json",
    )
    body = response.json()
    if "errors" in body:
        sys.stderr.write(json.dumps(body["errors"])[:2000] + "\n")
        sys.exit(1)
    count = len(body["data"]["categories"]["edges"])
sys.stdout.write(f"{count}\n")
