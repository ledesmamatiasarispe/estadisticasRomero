# sync_access.ps1 — Exporta todas las tablas de Access a CSVs
# Debe ejecutarse con SysWOW64\powershell.exe (32-bit) para acceder al driver ACE OLEDB 12.0
param(
    [string]$OutputDir = "C:\GNC_API\data\exports",
    [string]$DbPath    = "M:\bases\2011\datos\datosunificado2010.accdb"
)

$ErrorActionPreference = "Stop"

function Format-CsvValue($val) {
    if ($null -eq $val) { return "" }
    $s = $val.ToString()
    # Escapar comillas dobles y envolver en comillas si tiene comas, comillas o newlines
    if ($s -match '[,"\r\n]') {
        $s = '"' + $s.Replace('"', '""') + '"'
    }
    return $s
}

function Format-DateValue($val) {
    if ($val -is [DateTime]) {
        return $val.ToString("yyyy-MM-dd HH:mm:ss")
    }
    return $val
}

try {
    Write-Host "[sync_access] Conectando a la base de datos..."
    $connStr = "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=$DbPath;Persist Security Info=False;"
    $conn = New-Object System.Data.OleDb.OleDbConnection($connStr)
    $conn.Open()
    Write-Host "[sync_access] Conexion OK"

    # Crear directorio de salida si no existe
    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir | Out-Null
    }

    # Limpiar CSVs anteriores
    Get-ChildItem $OutputDir -Filter "*.csv" | Remove-Item -Force

    # Obtener lista de tablas
    $schema = $conn.GetSchema("Tables")
    $tables = @($schema | Where-Object { $_.TABLE_TYPE -eq "TABLE" } | Select-Object -ExpandProperty TABLE_NAME)
    Write-Host "[sync_access] Tablas encontradas: $($tables.Count)"

    $results = @()
    $i = 0

    foreach ($tableName in $tables) {
        $i++
        Write-Host "[sync_access] [$i/$($tables.Count)] $tableName"

        try {
            $cmd = $conn.CreateCommand()
            $cmd.CommandText = "SELECT * FROM [$tableName]"
            $cmd.CommandTimeout = 120
            $reader = $cmd.ExecuteReader()

            # Nombre de archivo: sanitizar caracteres especiales
            $safeName = $tableName `
                -replace '[\\/:*?"<>|]', '_' `
                -replace '\s+', '_'

            $csvPath = Join-Path $OutputDir "$safeName.csv"

            $sb = New-Object System.Text.StringBuilder
            $rowCount = 0

            # Cabecera
            $cols = @()
            for ($c = 0; $c -lt $reader.FieldCount; $c++) {
                $cols += $reader.GetName($c)
            }
            $sb.AppendLine(($cols | ForEach-Object { Format-CsvValue $_ }) -join ",") | Out-Null

            # Filas
            while ($reader.Read()) {
                $row = @()
                for ($c = 0; $c -lt $reader.FieldCount; $c++) {
                    $val = $reader.GetValue($c)
                    if ($reader.IsDBNull($c)) {
                        $row += ""
                    } elseif ($val -is [DateTime]) {
                        $row += Format-CsvValue (Format-DateValue $val)
                    } else {
                        $row += Format-CsvValue $val
                    }
                }
                $sb.AppendLine($row -join ",") | Out-Null
                $rowCount++
            }
            $reader.Close()

            # Escribir UTF-8 con BOM (para compatibilidad con Python utf-8-sig)
            $utf8bom = New-Object System.Text.UTF8Encoding($true)
            [System.IO.File]::WriteAllText($csvPath, $sb.ToString(), $utf8bom)

            $results += [PSCustomObject]@{
                table    = $tableName
                safe     = $safeName
                rows     = $rowCount
                ok       = $true
                error    = ""
            }
        }
        catch {
            Write-Host "[sync_access]   ERROR en tabla '$tableName': $_"
            $results += [PSCustomObject]@{
                table = $tableName
                safe  = ""
                rows  = 0
                ok    = $false
                error = $_.ToString()
            }
        }
    }

    $conn.Close()

    # Escribir tables.json con el resumen
    $json = $results | ConvertTo-Json -Depth 3
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $OutputDir "tables.json"), $json, $utf8)

    $ok    = ($results | Where-Object { $_.ok }).Count
    $total = $results.Count
    Write-Host "[sync_access] Completado: $ok/$total tablas exportadas OK"
    exit 0
}
catch {
    Write-Host "[sync_access] ERROR FATAL: $_"
    exit 1
}
