$ErrorActionPreference = "Stop"

$RESOURCE_GROUP = "traking-web"
$LOCATION = "centralus"
$SERVER_NAME = "phx-win-sql-$(Get-Random -Minimum 1000 -Maximum 9999)"
$DB_NAME = "BD_Phoenix"
$ADMIN_USER = "phxadmin"
$ADMIN_PASS = "WinTelecom@2026!"

Write-Host "1. Creando Azure SQL Server (esto toma unos minutos)..."
Write-Host "Nombre del servidor: $SERVER_NAME"
az sql server create --name $SERVER_NAME --resource-group $RESOURCE_GROUP --location $LOCATION --admin-user $ADMIN_USER --admin-password $ADMIN_PASS | Out-Null

Write-Host "2. Creando Base de Datos (Nivel Básico -$5/mes)..."
az sql db create --resource-group $RESOURCE_GROUP --server $SERVER_NAME --name $DB_NAME --service-objective Basic | Out-Null

Write-Host "3. Configurando Firewall (Permitir servicios de Azure)..."
# La regla 0.0.0.0 - 0.0.0.0 es el estándar de Azure para "Allow Azure Services"
az sql server firewall-rule create --resource-group $RESOURCE_GROUP --server $SERVER_NAME --name AllowAzureServices --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 | Out-Null

Write-Host "=========================================================="
Write-Host "¡BASE DE DATOS CREADA EXITOSAMENTE!"
Write-Host "Guarda estas credenciales:"
Write-Host "Servidor (MYSQL_HOST / AZURE_SERVER): $SERVER_NAME.database.windows.net"
Write-Host "Base de Datos (MYSQL_DATABASE): $DB_NAME"
Write-Host "Usuario (MYSQL_USER): $ADMIN_USER"
Write-Host "Contraseña (MYSQL_PASSWORD): $ADMIN_PASS"
Write-Host "=========================================================="