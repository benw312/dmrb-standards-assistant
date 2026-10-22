#!/usr/bin/env bash
# Deletes everything in the project's resource group. Run by hand.
# Rebuild afterwards with azure_setup.sh, then `python -m index.rebuild`.
set -euo pipefail

az group delete --name rg-dmrb-assistant --yes --no-wait
echo "Deletion started. Foundry/AI Services resources are soft-deleted; purge them so the names"
echo "can be reused:  az cognitiveservices account list-deleted -o table"
