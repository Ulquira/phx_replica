#!/bin/bash
set -e

RESOURCE_GROUP="traking-web"
LOCATION="eastus"
ACR_NAME="phxwinacr"
CONTAINERAPPS_ENV="phx-win-env"
CONTAINER_APP_NAME="phx-win-sync"
IMAGE_NAME="$ACR_NAME.azurecr.io/api-phx-sync:latest"

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI no está instalado. Instala Azure CLI primero."
  exit 1
fi

az account show >/dev/null
az group create --name "$RESOURCE_GROUP" --location "$LOCATION"
az acr create --resource-group "$RESOURCE_GROUP" --name "$ACR_NAME" --sku Basic --admin-enabled true
az acr login --name "$ACR_NAME"
docker build -t "$IMAGE_NAME" .
docker push "$IMAGE_NAME"

az containerapp env create \
  --name "$CONTAINERAPPS_ENV" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION"

az containerapp create \
  --name "$CONTAINER_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$CONTAINERAPPS_ENV" \
  --image "$IMAGE_NAME" \
  --ingress "internal" \
  --cpu 0.5 --memory 1.0Gi \
  --min-replicas 1 --max-replicas 1 \
  --env-vars "PYTHONUNBUFFERED=1" "SYNC_INTERVAL=60"

echo "Despliegue creado. Configura las variables de entorno en Azure Portal o con az containerapp update." 
