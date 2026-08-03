$ErrorActionPreference = "Stop"

$RESOURCE_GROUP = "traking-web"
$LOCATION = "eastus"
$ACR_NAME = "phxwinacr"
$CONTAINERAPPS_ENV = "phx-win-env"
$CONTAINER_APP_NAME = "phx-win-sync"
$IMAGE_NAME = "$ACR_NAME.azurecr.io/api-phx-sync:latest"

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error "Azure CLI no está instalado. Instala Azure CLI primero."
    exit 1
}

Write-Host "Verificando cuenta de Azure..."
az account show | Out-Null

Write-Host "Creando grupo de recursos..."
az group create --name $RESOURCE_GROUP --location $LOCATION | Out-Null

Write-Host "Creando Azure Container Registry (ACR)..."
az acr create --resource-group $RESOURCE_GROUP --name $ACR_NAME --sku Basic --admin-enabled true | Out-Null

Write-Host "Iniciando sesión en ACR..."
az acr login --name $ACR_NAME | Out-Null

Write-Host "Construyendo la imagen de Docker..."
docker build -t $IMAGE_NAME .

Write-Host "Subiendo la imagen a ACR..."
docker push $IMAGE_NAME

Write-Host "Creando entorno de Container Apps..."
az containerapp env create --name $CONTAINERAPPS_ENV --resource-group $RESOURCE_GROUP --location $LOCATION | Out-Null

Write-Host "Creando la Container App..."
az containerapp create `
  --name $CONTAINER_APP_NAME `
  --resource-group $RESOURCE_GROUP `
  --environment $CONTAINERAPPS_ENV `
  --image $IMAGE_NAME `
  --ingress "internal" `
  --cpu 0.5 --memory 1.0Gi `
  --min-replicas 1 --max-replicas 1 `
  --env-vars "PYTHONUNBUFFERED=1" "SYNC_INTERVAL=60" | Out-Null

Write-Host "¡Despliegue completado! Configura las variables de entorno en Azure Portal o con 'az containerapp update'."
