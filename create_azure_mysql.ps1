$ErrorActionPreference = "Stop"

$RESOURCE_GROUP = "traking-web"
$LOCATION = "centralus"
$SERVER_NAME = "phx-win-mysql-$(Get-Random -Minimum 1000 -Maximum 9999)"
$ADMIN_USER = "phxadmin"
$ADMIN_PASS = "WinTelecom@2026!"

Write-Host "1. Creando Azure Database for MySQL Flexible Server (B1s - ~$10/mes)..."
Write-Host "Nombre del servidor: $SERVER_NAME"
az mysql flexible-server create `
    --resource-group $RESOURCE_GROUP `
    --name $SERVER_NAME `
    --location $LOCATION `
    --admin-user $ADMIN_USER `
    --admin-password $ADMIN_PASS `
    --sku-name Standard_B1s `
    --tier Burstable `
    --public-access "0.0.0.0" | Out-Null

# Nota: El parámetro --public-access "0.0.0.0" habilita la regla "Allow public access from any Azure service within Azure to this server", que es exactamente lo que necesitamos.

Write-Host "2. Creando la base de datos BD_Phoenix..."
az mysql flexible-server db create `
    --resource-group $RESOURCE_GROUP `
    --server-name $SERVER_NAME `
    --database-name BD_Phoenix | Out-Null

Write-Host "=========================================================="
Write-Host "¡MYSQL CREADO EXITOSAMENTE!"
Write-Host "Servidor (MYSQL_HOST): $SERVER_NAME.mysql.database.azure.com"
Write-Host "Base de Datos (MYSQL_DATABASE): BD_Phoenix"
Write-Host "Usuario (MYSQL_USER): $ADMIN_USER"
Write-Host "Contraseña (MYSQL_PASSWORD): $ADMIN_PASS"
Write-Host "=========================================================="