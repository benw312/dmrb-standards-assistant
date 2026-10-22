#!/usr/bin/env bash
# Azure setup for the DMRB assistant. Run by hand, never by Claude Code.
#
#   SUFFIX=bw01 ./scripts/azure_setup.sh resources   # resource group, AI Search, Document Intelligence
#   SEARCH_LOCATION=uksouth SUFFIX=bw01 ./scripts/azure_setup.sh resources   # if Search is full in LOCATION
#   (create the Foundry resource + project + model deployments in the portal, same resource group)
#   SUFFIX=bw01 FOUNDRY_NAME=<foundry-resource-name> ./scripts/azure_setup.sh roles
#
# SUFFIX makes the globally-unique names unique. Use lowercase letters and digits.
# Safe to re-run: anything that already exists is skipped.
set -euo pipefail

: "${SUFFIX:?Set SUFFIX to a short lowercase string, e.g. SUFFIX=bw01}"

RG="rg-dmrb-assistant"
LOCATION="${LOCATION:-swedencentral}"  # must match the resource group's region
# Search may live in a different region from Foundry: regions can run out of Search capacity
# (Sweden Central did in Oct 2026), and the cross-region latency is a few milliseconds.
SEARCH_LOCATION="${SEARCH_LOCATION:-$LOCATION}"
SEARCH_NAME="srch-dmrb-${SUFFIX}"
DOCINTEL_NAME="di-dmrb-${SUFFIX}"

# True if the given `az ... show` command finds the resource.
exists() {
  "$@" --output none 2>/dev/null
}

create_resources() {
  # Safe to re-run if you already made the group in the portal (same name and region).
  az group create --name "$RG" --location "$LOCATION" --output none

  # Free tier: 3 indexes, 50 MB, free semantic ranker allowance. aadOrApiKey enables RBAC so
  # code can use DefaultAzureCredential instead of keys.
  if exists az search service show --name "$SEARCH_NAME" --resource-group "$RG"; then
    echo "AI Search $SEARCH_NAME already exists, skipping"
  else
    az search service create \
      --name "$SEARCH_NAME" --resource-group "$RG" --location "$SEARCH_LOCATION" \
      --sku free --semantic-search free \
      --auth-options aadOrApiKey --aad-auth-failure-mode http401WithBearerChallenge --output none
  fi

  # S0, not F0: the free tier only analyses the first 2 pages of each document, and DMRB
  # standards run to 50-100 pages. Layout costs roughly $10 per 1,000 pages, so a few dollars.
  # --custom-domain is required for Entra ID (token) auth.
  if exists az cognitiveservices account show --name "$DOCINTEL_NAME" --resource-group "$RG"; then
    echo "Document Intelligence $DOCINTEL_NAME already exists, skipping"
  else
    az cognitiveservices account create \
      --name "$DOCINTEL_NAME" --resource-group "$RG" --location "$LOCATION" \
      --kind FormRecognizer --sku S0 --custom-domain "$DOCINTEL_NAME" --yes --output none
  fi

  echo
  echo "AZURE_SEARCH_ENDPOINT=https://${SEARCH_NAME}.search.windows.net"
  echo "AZURE_DOCINTEL_ENDPOINT=https://${DOCINTEL_NAME}.cognitiveservices.azure.com/"
}

# "Foundry User" (formerly "Azure AI User"). Microsoft renamed it in 2026, so use the fixed ID.
FOUNDRY_USER_ROLE="53ca6127-db72-4b80-b1b0-d745d6d5456d"

assign() {
  local assignee="$1" role="$2" scope="$3"
  az role assignment create --assignee-object-id "$assignee" --assignee-principal-type "$4" \
    --role "$role" --scope "$scope" --output none
  echo "  $role -> $(basename "$scope")"
}

assign_roles() {
  : "${FOUNDRY_NAME:?Set FOUNDRY_NAME to the Foundry resource name from the portal}"

  local me search_id docintel_id foundry_id foundry_mi
  me=$(az ad signed-in-user show --query id -o tsv)
  search_id=$(az search service show -n "$SEARCH_NAME" -g "$RG" --query id -o tsv)
  docintel_id=$(az cognitiveservices account show -n "$DOCINTEL_NAME" -g "$RG" --query id -o tsv)
  foundry_id=$(az cognitiveservices account show -n "$FOUNDRY_NAME" -g "$RG" --query id -o tsv)
  foundry_mi=$(az cognitiveservices account show -n "$FOUNDRY_NAME" -g "$RG" --query identity.principalId -o tsv)

  # You (local dev via az login). Owner/Contributor do not grant data-plane access, hence these.
  echo "Roles for you:"
  assign "$me" "Search Index Data Contributor" "$search_id" User
  assign "$me" "Search Service Contributor" "$search_id" User
  assign "$me" "Cognitive Services User" "$docintel_id" User
  assign "$me" "$FOUNDRY_USER_ROLE" "$foundry_id" User

  # Foundry's managed identity, so the built-in Azure AI Search tool can query the index.
  echo "Roles for the Foundry managed identity:"
  assign "$foundry_mi" "Search Index Data Contributor" "$search_id" ServicePrincipal
  assign "$foundry_mi" "Search Service Contributor" "$search_id" ServicePrincipal
}

case "${1:-}" in
  resources) create_resources ;;
  roles) assign_roles ;;
  *) echo "Usage: SUFFIX=... $0 resources | SUFFIX=... FOUNDRY_NAME=... $0 roles" >&2; exit 1 ;;
esac
