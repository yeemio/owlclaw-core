$ErrorActionPreference = "Stop"

Push-Location "owlclaw/web/frontend"
try {
    npm install
    npm run build
}
finally {
    Pop-Location
}

Write-Host "Console build complete: owlclaw/web/static/"
