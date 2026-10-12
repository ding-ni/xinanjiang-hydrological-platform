param(
  [string]$DatabasePath = (Join-Path $PSScriptRoot '..\..\..\实际案例\南安溪-徐畈案例\02_徐畈水文与模型资料\xufan.mdb'),
  [string]$FloodWorkbookPath = (Join-Path $PSScriptRoot '..\..\..\实际案例\南安溪-徐畈案例\02_徐畈水文与模型资料\floodcode.xlsx'),
  [string]$OutputRoot = (Join-Path $PSScriptRoot '..\public\data\projects\nananxi-xufan\observations'),
  [string]$FloodFixturePath = (Join-Path $PSScriptRoot '..\src\fixtures\catalogs\flood-events.json'),
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$invariant = [System.Globalization.CultureInfo]::InvariantCulture
$utf8 = New-Object System.Text.UTF8Encoding($false)
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
$DatabasePath = [System.IO.Path]::GetFullPath($DatabasePath)
$FloodWorkbookPath = [System.IO.Path]::GetFullPath($FloodWorkbookPath)
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$rainRoot = Join-Path $OutputRoot 'rain'
$riverRoot = Join-Path $OutputRoot 'river'
$previewRoot = Join-Path $OutputRoot 'previews'
New-Item -ItemType Directory -Path $rainRoot, $riverRoot, $previewRoot -Force | Out-Null

function Get-PortableResourceUrl([string]$TargetPath) {
  $fullTarget = [System.IO.Path]::GetFullPath($TargetPath)
  $relative = [System.IO.Path]::GetRelativePath($OutputRoot, $fullTarget) -replace '\\', '/'
  if ($relative.StartsWith('./')) { return $relative }
  return "./$relative"
}

function Write-Utf8Json([string]$Path, $Value, [int]$Depth = 10) {
  $json = $Value | ConvertTo-Json -Depth $Depth -Compress
  [System.IO.File]::WriteAllText($Path, $json, $utf8)
}

function Get-PortableSourceReference([string]$Path) {
  $fullPath = [System.IO.Path]::GetFullPath($Path)
  $relative = [System.IO.Path]::GetRelativePath($projectRoot, $fullPath)
  if (-not $relative.StartsWith('..')) { return ($relative -replace '\\', '/') }
  return [System.IO.Path]::GetFileName($fullPath)
}

function Format-Number($Value) {
  if ($null -eq $Value -or $Value -is [System.DBNull]) { return 'null' }
  return [Convert]::ToDouble($Value).ToString('0.###', $invariant)
}

function New-DateParameter($Command, [datetime]$Value) {
  $parameter = $Command.Parameters.Add('?', [System.Data.OleDb.OleDbType]::Date)
  $parameter.Value = $Value
}

function Get-Table([System.Data.OleDb.OleDbConnection]$Connection, [string]$Sql) {
  $command = $Connection.CreateCommand()
  $command.CommandText = $Sql
  $adapter = New-Object System.Data.OleDb.OleDbDataAdapter($command)
  $table = New-Object System.Data.DataTable
  [void]$adapter.Fill($table)
  return $table
}

function Get-Scalar([System.Data.OleDb.OleDbConnection]$Connection, [string]$Sql) {
  $command = $Connection.CreateCommand()
  $command.CommandText = $Sql
  return $command.ExecuteScalar()
}

if (-not (Test-Path -LiteralPath $DatabasePath -PathType Leaf)) { throw "MDB 不存在：$DatabasePath" }
if (-not (Test-Path -LiteralPath $FloodWorkbookPath -PathType Leaf)) { throw "洪水目录不存在：$FloodWorkbookPath" }

$connection = New-Object System.Data.OleDb.OleDbConnection("Provider=Microsoft.ACE.OLEDB.12.0;Data Source=$DatabasePath;Mode=Read;")
$connection.Open()

$spatialCodeByLegacy = @{
  '87001'='7011AJ72'; '87002'='70149600'; '87003'='70116150'; '87004'='70149720'
  '87005'='70149640'; '87006'='70149680'; '87007'='7011AJ99'; '87008'='7011AJ2C'
  '87009'='7011AJ64'; '87010'='701AAJ02'; '87011'='7011AJ6Q'; '87012'='70115800'
}

$rainCounts = @{}
foreach ($row in (Get-Table $connection 'SELECT HS_Code, COUNT(*) AS N, MIN(YMDHM) AS MinTime, MAX(YMDHM) AS MaxTime FROM ReFactorDT GROUP BY HS_Code')) {
  $rainCounts[[string]$row.HS_Code] = [ordered]@{
    records = [int64]$row.N
    start = ([datetime]$row.MinTime).ToString('yyyy-MM-ddTHH:mm')
    end = ([datetime]$row.MaxTime).ToString('yyyy-MM-ddTHH:mm')
  }
}

$rainStations = @()
foreach ($row in (Get-Table $connection 'SELECT p_code, out_pcode, p_name FROM rain_gauge ORDER BY p_number')) {
  $legacyCode = [string]$row.p_code
  $coverage = $rainCounts[$legacyCode]
  $rainStations += [ordered]@{
    legacyCode = $legacyCode
    sourceCode = [string]$row.out_pcode
    spatialCode = $spatialCodeByLegacy[$legacyCode]
    name = [string]$row.p_name
    records = if ($coverage) { $coverage.records } else { 0 }
    start = if ($coverage) { $coverage.start } else { $null }
    end = if ($coverage) { $coverage.end } else { $null }
  }
}
$stationIndex = @{}
for ($i = 0; $i -lt $rainStations.Count; $i++) { $stationIndex[$rainStations[$i].legacyCode] = $i }

$rainYears = @()
$rainYearRows = Get-Table $connection 'SELECT Year(YMDHM) AS DataYear, COUNT(*) AS N, MIN(YMDHM) AS MinTime, MAX(YMDHM) AS MaxTime FROM ReFactorDT GROUP BY Year(YMDHM) ORDER BY Year(YMDHM)'
foreach ($yearRow in $rainYearRows) {
  $year = [int]$yearRow.DataYear
  $yearStart = [datetime]::new($year, 1, 1)
  $yearEnd = $yearStart.AddYears(1)
  $path = Join-Path $rainRoot "$year.json"
  if (-not $Force -and (Test-Path -LiteralPath $path -PathType Leaf) -and (Get-Item -LiteralPath $path).Length -gt 50) {
    $rainYears += [ordered]@{
      year = $year; url = (Get-PortableResourceUrl (Join-Path $rainRoot "$year.json")); records = [int64]$yearRow.N
      start = ([datetime]$yearRow.MinTime).ToString('yyyy-MM-ddTHH:mm'); end = ([datetime]$yearRow.MaxTime).ToString('yyyy-MM-ddTHH:mm'); bytes = (Get-Item -LiteralPath $path).Length
    }
    Write-Host "复用雨量年度文件：$year"
    continue
  }
  $writer = New-Object System.IO.StreamWriter($path, $false, $utf8, 65536)
  try {
    $writer.Write('{"schemaVersion":"1.0","year":')
    $writer.Write($year)
    $writer.Write(',"timeEncoding":"hour-of-year-local","stationCodes":')
    $writer.Write(($rainStations.legacyCode | ConvertTo-Json -Compress))
    $writer.Write(',"columns":["hour","stationIndex","rainMm"],"rows":[')
    $command = $connection.CreateCommand()
    $command.CommandText = 'SELECT HS_Code,YMDHM,DTRN FROM ReFactorDT WHERE YMDHM >= ? AND YMDHM < ? ORDER BY YMDHM,HS_Code'
    New-DateParameter $command $yearStart
    New-DateParameter $command $yearEnd
    $reader = $command.ExecuteReader()
    $first = $true
    try {
      while ($reader.Read()) {
        $legacyCode = [string]$reader.GetValue(0)
        if (-not $stationIndex.ContainsKey($legacyCode)) { continue }
        if ($first) { $first = $false } else { $writer.Write(',') }
        $hour = [int][Math]::Round((([datetime]$reader.GetValue(1)) - $yearStart).TotalHours)
        $writer.Write('['); $writer.Write($hour); $writer.Write(','); $writer.Write($stationIndex[$legacyCode]); $writer.Write(','); $writer.Write((Format-Number $reader.GetValue(2))); $writer.Write(']')
      }
    } finally { $reader.Close() }
    $writer.Write(']}')
  } finally { $writer.Dispose() }
  $rainYears += [ordered]@{
    year = $year
    url = (Get-PortableResourceUrl $path)
    records = [int64]$yearRow.N
    start = ([datetime]$yearRow.MinTime).ToString('yyyy-MM-ddTHH:mm')
    end = ([datetime]$yearRow.MaxTime).ToString('yyyy-MM-ddTHH:mm')
    bytes = (Get-Item -LiteralPath $path).Length
  }
  Write-Host "雨量年度文件：$year ($($yearRow.N) 条)"
}

$riverYears = @()
$riverYearRows = Get-Table $connection 'SELECT Year(ymdhm) AS DataYear, COUNT(*) AS N, MIN(ymdhm) AS MinTime, MAX(ymdhm) AS MaxTime FROM st_river_r GROUP BY Year(ymdhm) ORDER BY Year(ymdhm)'
foreach ($yearRow in $riverYearRows) {
  $year = [int]$yearRow.DataYear
  $yearStart = [datetime]::new($year, 1, 1)
  $yearEnd = $yearStart.AddYears(1)
  $path = Join-Path $riverRoot "$year.json"
  if (-not $Force -and (Test-Path -LiteralPath $path -PathType Leaf) -and (Get-Item -LiteralPath $path).Length -gt 50) {
    $riverYears += [ordered]@{
      year = $year; url = (Get-PortableResourceUrl (Join-Path $riverRoot "$year.json")); records = [int64]$yearRow.N
      start = ([datetime]$yearRow.MinTime).ToString('yyyy-MM-ddTHH:mm'); end = ([datetime]$yearRow.MaxTime).ToString('yyyy-MM-ddTHH:mm'); bytes = (Get-Item -LiteralPath $path).Length
    }
    Write-Host "复用水位流量年度文件：$year"
    continue
  }
  $writer = New-Object System.IO.StreamWriter($path, $false, $utf8, 65536)
  try {
    $writer.Write('{"schemaVersion":"1.0","year":'); $writer.Write($year)
    $writer.Write(',"timeEncoding":"hour-of-year-local","station":{"sourceCode":"CGB087012","spatialCode":"70115800","name":"徐畈（二）"},"columns":["hour","levelM","flowM3s"],"rows":[')
    $command = $connection.CreateCommand()
    $command.CommandText = 'SELECT ymdhm,zr,q FROM st_river_r WHERE ymdhm >= ? AND ymdhm < ? ORDER BY ymdhm'
    New-DateParameter $command $yearStart
    New-DateParameter $command $yearEnd
    $reader = $command.ExecuteReader()
    $first = $true
    try {
      while ($reader.Read()) {
        if ($first) { $first = $false } else { $writer.Write(',') }
        $hour = [int][Math]::Round((([datetime]$reader.GetValue(0)) - $yearStart).TotalHours)
        $writer.Write('['); $writer.Write($hour); $writer.Write(','); $writer.Write((Format-Number $reader.GetValue(1))); $writer.Write(','); $writer.Write((Format-Number $reader.GetValue(2))); $writer.Write(']')
      }
    } finally { $reader.Close() }
    $writer.Write(']}')
  } finally { $writer.Dispose() }
  $riverYears += [ordered]@{
    year = $year
    url = (Get-PortableResourceUrl $path)
    records = [int64]$yearRow.N
    start = ([datetime]$yearRow.MinTime).ToString('yyyy-MM-ddTHH:mm')
    end = ([datetime]$yearRow.MaxTime).ToString('yyyy-MM-ddTHH:mm')
    bytes = (Get-Item -LiteralPath $path).Length
  }
  Write-Host "水位流量年度文件：$year ($($yearRow.N) 条)"
}

$riverTotal = [int64](Get-Scalar $connection 'SELECT COUNT(*) FROM st_river_r')
$riverMin = [datetime](Get-Scalar $connection 'SELECT MIN(ymdhm) FROM st_river_r')
$riverMax = [datetime](Get-Scalar $connection 'SELECT MAX(ymdhm) FROM st_river_r')
$rainTotal = [int64](Get-Scalar $connection 'SELECT COUNT(*) FROM ReFactorDT')
$rainMin = [datetime](Get-Scalar $connection 'SELECT MIN(YMDHM) FROM ReFactorDT')
$rainMax = [datetime](Get-Scalar $connection 'SELECT MAX(YMDHM) FROM ReFactorDT')
$peakCommand = $connection.CreateCommand()
$peakCommand.CommandText = 'SELECT TOP 1 ymdhm FROM st_river_r WHERE ymdhm >= ? AND ymdhm <= ? ORDER BY q DESC, ymdhm DESC'
New-DateParameter $peakCommand ([datetime]::new($riverMax.Year, 1, 1))
New-DateParameter $peakCommand $riverMax
$peakTime = [datetime]$peakCommand.ExecuteScalar()
$previewStart = $peakTime.AddHours(-35)
$previewEnd = [datetime]::new([Math]::Min($riverMax.Ticks, $peakTime.AddHours(36).Ticks))
$times = @(for ($time = $previewStart; $time -le $previewEnd; $time = $time.AddHours(1)) { $time.ToString('yyyy-MM-ddTHH:mm') })
$rainPreview = [ordered]@{}
foreach ($station in $rainStations) { $rainPreview[$station.legacyCode] = @($null) * $times.Count }
$rainCommand = $connection.CreateCommand()
$rainCommand.CommandText = 'SELECT HS_Code,YMDHM,DTRN FROM ReFactorDT WHERE YMDHM >= ? AND YMDHM <= ? ORDER BY YMDHM,HS_Code'
New-DateParameter $rainCommand $previewStart
New-DateParameter $rainCommand $previewEnd
$reader = $rainCommand.ExecuteReader()
try {
  while ($reader.Read()) {
    $code = [string]$reader.GetValue(0)
    if (-not $rainPreview.Contains($code)) { continue }
    $index = [int][Math]::Round((([datetime]$reader.GetValue(1)) - $previewStart).TotalHours)
    if ($index -ge 0 -and $index -lt $times.Count) { $rainPreview[$code][$index] = [Math]::Round([double]$reader.GetValue(2), 3) }
  }
} finally { $reader.Close() }
$levels = @($null) * $times.Count
$flows = @($null) * $times.Count
$riverCommand = $connection.CreateCommand()
$riverCommand.CommandText = 'SELECT ymdhm,zr,q FROM st_river_r WHERE ymdhm >= ? AND ymdhm <= ? ORDER BY ymdhm'
New-DateParameter $riverCommand $previewStart
New-DateParameter $riverCommand $previewEnd
$reader = $riverCommand.ExecuteReader()
try {
  while ($reader.Read()) {
    $index = [int][Math]::Round((([datetime]$reader.GetValue(0)) - $previewStart).TotalHours)
    if ($index -ge 0 -and $index -lt $times.Count) {
      $levels[$index] = if ($reader.IsDBNull(1)) { $null } else { [Math]::Round([double]$reader.GetValue(1), 3) }
      $flows[$index] = if ($reader.IsDBNull(2)) { $null } else { [Math]::Round([double]$reader.GetValue(2), 3) }
    }
  }
} finally { $reader.Close() }
$connection.Close()

$excelConnection = New-Object System.Data.OleDb.OleDbConnection("Provider=Microsoft.ACE.OLEDB.12.0;Data Source=$FloodWorkbookPath;Extended Properties='Excel 12.0 Xml;HDR=YES;IMEX=1';")
$excelConnection.Open()
$events = @()
$eventCommand = $excelConnection.CreateCommand()
$eventCommand.CommandText = 'SELECT [洪号],[起始时间],[结束时间],[周期长度(小时)],[洪峰值] FROM [Sheet1$] WHERE [洪号] IS NOT NULL ORDER BY [起始时间]'
$eventReader = $eventCommand.ExecuteReader()
try {
  while ($eventReader.Read()) {
    $floodCode = [string]$eventReader.GetValue(0)
    $start = [datetime]$eventReader.GetValue(1)
    $end = [datetime]$eventReader.GetValue(2)
    $events += [ordered]@{
      id = "xf-$floodCode"
      basinProjectId = 'nananxi-xufan'
      name = "$floodCode 号洪水"
      purpose = '未使用'
      start = $start.ToString('yyyy-MM-ddTHH:mm')
      end = $end.ToString('yyyy-MM-ddTHH:mm')
      durationHours = if ($eventReader.IsDBNull(3)) { [int][Math]::Round(($end - $start).TotalHours) } else { [int]$eventReader.GetValue(3) }
      observedPeak = if ($eventReader.IsDBNull(4)) { $null } else { [Math]::Round([double]$eventReader.GetValue(4), 3) }
      note = '徐畈实测洪水场次目录'
      source = 'floodcode.xlsx'
    }
  }
} finally { $eventReader.Close(); $excelConnection.Close() }

$preview = [ordered]@{
  schemaVersion = '1.0'
  source = 'xufan.mdb'
  status = 'observed'
  start = $previewStart.ToString('yyyy-MM-ddTHH:mm')
  end = $previewEnd.ToString('yyyy-MM-ddTHH:mm')
  stepHours = 1
  times = $times
  rain = $rainPreview
  river = [ordered]@{ spatialCode='70115800'; sourceCode='CGB087012'; name='徐畈（二）'; levels=$levels; flows=$flows }
}
Write-Utf8Json (Join-Path $previewRoot 'latest-72h.json') $preview 8

$eventCatalog = [ordered]@{
  schemaVersion = '1.0'
  source = 'floodcode.xlsx'
  count = $events.Count
  events = $events
}
Write-Utf8Json (Join-Path $OutputRoot 'flood-events.json') $eventCatalog 8

$catalog = [ordered]@{
  schemaVersion = '1.0'
  generatedAt = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
  source = [ordered]@{ database=(Get-PortableSourceReference $DatabasePath); floodWorkbook=(Get-PortableSourceReference $FloodWorkbookPath); mode='read-only conversion' }
  rain = [ordered]@{
    datasetId = 'XF-OBS-RAIN-HOURLY'
    status = 'observed'
    unit = 'mm'
    totalRecords = $rainTotal
    start = $rainMin.ToString('yyyy-MM-ddTHH:mm')
    end = $rainMax.ToString('yyyy-MM-ddTHH:mm')
    stations = $rainStations
    years = $rainYears
  }
  river = [ordered]@{
    datasetId = 'XF-OBS-RIVER-HOURLY'
    status = 'observed'
    station = [ordered]@{ sourceCode='CGB087012'; legacyCode='87012'; spatialCode='70115800'; name='徐畈（二）' }
    variables = @([ordered]@{key='level';sourceField='zr';unit='m'},[ordered]@{key='flow';sourceField='q';unit='m3/s'})
    totalRecords = $riverTotal
    start = $riverMin.ToString('yyyy-MM-ddTHH:mm')
    end = $riverMax.ToString('yyyy-MM-ddTHH:mm')
    years = $riverYears
  }
  floodEvents = [ordered]@{ datasetId='XF-FLOOD-EVENTS'; status='observed-index'; count=$events.Count; url=(Get-PortableResourceUrl (Join-Path $OutputRoot 'flood-events.json')) }
  latestPreview = (Get-PortableResourceUrl (Join-Path $previewRoot 'latest-72h.json'))
}
Write-Utf8Json (Join-Path $OutputRoot 'catalog.json') $catalog 12

$fixture = Get-Content -LiteralPath $FloodFixturePath -Raw -Encoding UTF8 | ConvertFrom-Json
$preserved = @($fixture.events | Where-Object { $_.basinProjectId -ne 'nananxi-xufan' })
$fixture.events = @($preserved + $events)
$fixture.version = (Get-Date).ToString('yyyy-MM-dd')
Write-Utf8Json $FloodFixturePath $fixture 10

[pscustomobject]@{
  RainRecords = $rainTotal
  RiverRecords = $riverTotal
  FloodEvents = $events.Count
  RainYears = $rainYears.Count
  RiverYears = $riverYears.Count
  OutputRoot = $OutputRoot
} | Format-List
