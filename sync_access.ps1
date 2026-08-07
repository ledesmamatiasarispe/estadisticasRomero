# sync_access.ps1 — Exporta tablas de Access a CSVs (con sync incremental/por grupo)
# Debe ejecutarse con SysWOW64\powershell.exe (32-bit) para acceder al driver ACE OLEDB 12.0
param(
    [string]$OutputDir     = "C:\GNC_API\data\exports",
    [string]$DbPath        = "M:\bases\2011\datos\datosunificado2010.accdb",
    # Filtro de tablas: si no está vacío, solo exporta las tablas de la lista (separadas por coma).
    [string]$Tables        = "",
    # Meses hacia atrás para el filtro de fecha de Trabajos (solo aplica en modo grupo).
    [int]   $RollingMonths = 3,
    # Cuando $true, ignora filtros de fecha/ID y exporta todo completo.
    [switch]$FullRefresh
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

    # -- Parsear filtro de tablas -----------------------------------------------
    $filterSet = @()
    if ($Tables -and $Tables.Trim() -ne "") {
        $filterSet = $Tables -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }
        Write-Host "[sync_access] Modo grupo: $($filterSet.Count) tablas filtradas ($($filterSet -join ', '))"
    } else {
        Write-Host "[sync_access] Modo completo: exportando todas las tablas."
    }
    $isGroupMode = $filterSet.Count -gt 0

    # -- Cargar watermarks para sync incremental --------------------------------
    $watermarks = @{}
    $wmPath = Join-Path $OutputDir "watermarks.json"
    if (-not $FullRefresh -and (Test-Path $wmPath)) {
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
    } elseif ($FullRefresh) {
        Write-Host "[sync_access] FullRefresh: ignorando watermarks."
    } else {
        Write-Host "[sync_access] Sin watermarks.json - sync completo para todas las tablas."
    }

    # Limpiar CSVs del run anterior (solo los que corresponden al grupo actual o todos)
    if ($isGroupMode) {
        foreach ($tbl in $filterSet) {
            $safeTbl = $tbl -replace '[\\/:*?"<>|]', '_' -replace '\s+', '_'
            $csvToClean = Join-Path $OutputDir "$safeTbl.csv"
            if (Test-Path $csvToClean) { Remove-Item $csvToClean -Force }
        }
    } else {
        Get-ChildItem $OutputDir -Filter "*.csv" | Remove-Item -Force
    }

    $schema = $conn.GetSchema("Tables")
    $allTables = @($schema | Where-Object { $_.TABLE_TYPE -eq "TABLE" } | Select-Object -ExpandProperty TABLE_NAME)

    # Aplicar filtro de grupo si corresponde
    if ($isGroupMode) {
        $filterLower = @($filterSet | ForEach-Object { $_.ToLower() })
        $matched = [System.Collections.Generic.List[string]]::new()
        foreach ($t in $allTables) {
            $tl = $t.ToLower()
            foreach ($f in $filterLower) {
                if ($f -eq $tl) { $matched.Add($t); break }
            }
        }
        $tblList = $matched.ToArray()
    } else {
        $tblList = $allTables
    }
    Write-Host "[sync_access] Tablas a exportar: $($tblList.Count)"

    $results = @()
    $i = 0

    foreach ($tableName in $tblList) {
        $i++

        try {
            $cmd = $conn.CreateCommand()
            $cmd.CommandTimeout = 180

            $isIncremental = $false
            $isDateRolling = $false

            # Trabajos en modo grupo (no full refresh): filtrar por fecha en Access
            if ($tableName -eq "Trabajos" -and $isGroupMode -and -not $FullRefresh) {
                $cmd.CommandText = "SELECT * FROM [Trabajos] WHERE [fechacargaot] >= DateAdd('m', -$RollingMonths, Now())"
                $isDateRolling = $true
                Write-Host "[sync_access] [$i/$($tblList.Count)] $tableName  (fecha >= -${RollingMonths}m)"
            } else {
                # Verificar watermark para sync incremental
                $wm = if (-not $FullRefresh) { $watermarks[$tableName] } else { $null }

                if ($null -ne $wm) {
                    $pkCol  = $wm.pk_col
                    $maxVal = $wm.max_val
                    $cmd.CommandText = "SELECT * FROM [$tableName] WHERE [$pkCol] > $maxVal"
                    $isIncremental = $true
                    Write-Host "[sync_access] [$i/$($tblList.Count)] $tableName  (+delta, $pkCol > $maxVal)"
                } else {
                    $cmd.CommandText = "SELECT * FROM [$tableName]"
                    Write-Host "[sync_access] [$i/$($tblList.Count)] $tableName"
                }
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

            if ($isDateRolling) {
                Write-Host "[sync_access]   -> $rowCount filas (ultimos $RollingMonths meses)"
            } elseif ($isIncremental) {
                Write-Host "[sync_access]   -> $rowCount filas nuevas"
            }

            $results += [PSCustomObject]@{
                table        = $tableName
                safe         = $safeName
                rows         = $rowCount
                ok           = $true
                error        = ""
                incremental  = $isIncremental
                date_rolling = $isDateRolling
            }
        }
        catch {
            Write-Host "[sync_access]   ERROR en tabla '$tableName': $_"
            $results += [PSCustomObject]@{
                table        = $tableName
                safe         = ""
                rows         = 0
                ok           = $false
                error        = $_.ToString()
                incremental  = $false
                date_rolling = $false
            }
        }
    }

    $conn.Close()

    $json = $results | ConvertTo-Json -Depth 3
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $OutputDir "tables.json"), $json, $utf8)

    $ok          = ($results | Where-Object { $_.ok }).Count
    $total       = $results.Count
    $incr        = ($results | Where-Object { $_.ok -and $_.incremental }).Count
    $dateRoll    = ($results | Where-Object { $_.ok -and $_.date_rolling }).Count
    $completo    = ($results | Where-Object { $_.ok -and -not $_.incremental -and -not $_.date_rolling }).Count
    Write-Host "[sync_access] Completado: $ok/$total tablas OK ($incr incrementales, $dateRoll date-rolling, $completo completas)"
    exit 0
}
catch {
    Write-Host "[sync_access] ERROR FATAL: $_"
    exit 1
}
