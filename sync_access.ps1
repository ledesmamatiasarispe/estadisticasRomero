# sync_access.ps1 - Exporta tablas de Access a CSVs (con sync incremental)
# Debe ejecutarse con SysWOW64\powershell.exe (32-bit) para acceder al driver ACE OLEDB 12.0
param(
    [string]$OutputDir = "C:\GNC_API\data\exports",
    [string]$DbPath    = "M:\bases\2011\datos\datosunificado2010.accdb"
)

$ErrorActionPreference = "Stop"

function Format-CsvValue($val) {
    if ($null -eq $val) { return "" }
    $s = $val.ToString()
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

    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir | Out-Null
    }

    # -- Cargar watermarks para sync incremental --------------------------------
    $watermarks = @{}
    $wmPath = Join-Path $OutputDir "watermarks.json"
    if (Test-Path $wmPath) {
        try {
            $wmJson = Get-Content $wmPath -Raw -Encoding UTF8
            $wmData = $wmJson | ConvertFrom-Json
            foreach ($prop in $wmData.PSObject.Properties) {
                $wm = $wmData.($prop.Name)
                if ($wm.max_val -gt 0) {
                    $watermarks[$prop.Name] = @{
                        pk_col  = $wm.pk_col
                        max_val = $wm.max_val
                    }
                }
            }
            Write-Host "[sync_access] Watermarks: $($watermarks.Count) tablas en modo incremental."
        } catch {
            Write-Host "[sync_access] Advertencia: no se pudo leer watermarks.json ($_). Usando sync completo."
            $watermarks = @{}
        }
    } else {
        Write-Host "[sync_access] Sin watermarks.json - sync completo para todas las tablas."
    }

    # Limpiar CSVs anteriores solo para tablas que haran sync completo
    # (los incrementales se sobreescriben de todos modos, pero es mas seguro limpiar todo)
    Get-ChildItem $OutputDir -Filter "*.csv" | Remove-Item -Force

    $schema = $conn.GetSchema("Tables")
    $tables = @($schema | Where-Object { $_.TABLE_TYPE -eq "TABLE" } | Select-Object -ExpandProperty TABLE_NAME)
    Write-Host "[sync_access] Tablas encontradas: $($tables.Count)"

    $results = @()
    $i = 0

    foreach ($tableName in $tables) {
        $i++

        try {
            $cmd = $conn.CreateCommand()
            $cmd.CommandTimeout = 120

            # Verificar si esta tabla tiene watermark para sync incremental
            $wm = $watermarks[$tableName]
            $isIncremental = $false

            if ($null -ne $wm) {
                $pkCol  = $wm.pk_col
                $maxVal = $wm.max_val
                $cmd.CommandText = "SELECT * FROM [$tableName] WHERE [$pkCol] > $maxVal"
                $isIncremental = $true
                Write-Host "[sync_access] [$i/$($tables.Count)] $tableName  (+delta, $pkCol > $maxVal)"
            } else {
                $cmd.CommandText = "SELECT * FROM [$tableName]"
                Write-Host "[sync_access] [$i/$($tables.Count)] $tableName"
            }

            $reader = $cmd.ExecuteReader()

            $safeName = $tableName `
                -replace '[\\/:*?"<>|]', '_' `
                -replace '\s+', '_'

            $csvPath = Join-Path $OutputDir "$safeName.csv"

            $sb = New-Object System.Text.StringBuilder
            $rowCount = 0

            $cols = @()
            for ($c = 0; $c -lt $reader.FieldCount; $c++) {
                $cols += $reader.GetName($c)
            }
            $sb.AppendLine(($cols | ForEach-Object { Format-CsvValue $_ }) -join ",") | Out-Null

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

            $utf8bom = New-Object System.Text.UTF8Encoding($true)
            [System.IO.File]::WriteAllText($csvPath, $sb.ToString(), $utf8bom)

            if ($isIncremental) {
                Write-Host "[sync_access]   -> $rowCount filas nuevas"
            }

            $results += [PSCustomObject]@{
                table       = $tableName
                safe        = $safeName
                rows        = $rowCount
                ok          = $true
                error       = ""
                incremental = $isIncremental
            }
        }
        catch {
            Write-Host "[sync_access]   ERROR en tabla '$tableName': $_"
            $results += [PSCustomObject]@{
                table       = $tableName
                safe        = ""
                rows        = 0
                ok          = $false
                error       = $_.ToString()
                incremental = $false
            }
        }
    }

    $conn.Close()

    $json = $results | ConvertTo-Json -Depth 3
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $OutputDir "tables.json"), $json, $utf8)

    $ok        = ($results | Where-Object { $_.ok }).Count
    $total     = $results.Count
    $incr      = ($results | Where-Object { $_.ok -and $_.incremental }).Count
    $completo  = ($results | Where-Object { $_.ok -and -not $_.incremental }).Count
    Write-Host "[sync_access] Completado: $ok/$total tablas OK ($incr incrementales, $completo completas)"
    exit 0
}
catch {
    Write-Host "[sync_access] ERROR FATAL: $_"
    exit 1
}
