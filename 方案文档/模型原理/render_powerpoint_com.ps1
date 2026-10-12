
param(
  [Parameter(Mandatory = $true)]
  [string]$Pptx,

  [Parameter(Mandatory = $true)]
  [string]$OutDir,

  [Parameter(Mandatory = $true)]
  [string]$RawReport,

  [Parameter(Mandatory = $false)]
  [int]$Dpi = 150
)

$ErrorActionPreference = "Stop"

function Release-ComObject {
  param($Object)
  if ($null -ne $Object) {
    try {
      [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Object)
    } catch {
      try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($Object) } catch {}
    }
  }
}

$pptxPath = (Resolve-Path -LiteralPath $Pptx).Path
$outPath = [System.IO.Path]::GetFullPath($OutDir)
$rawReportPath = [System.IO.Path]::GetFullPath($RawReport)

$reportObject = [ordered]@{
  status = "unknown"
  pptx = $pptxPath
  out_dir = $outPath
  renderer = "powerpoint"
  backend = "powershell-com"
  dpi = $Dpi
}

$app = $null
$presentation = $null

try {
  New-Item -ItemType Directory -Force -Path $outPath | Out-Null
  New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetDirectoryName($rawReportPath)) | Out-Null

  $app = New-Object -ComObject PowerPoint.Application
  try { $app.Visible = $true } catch {}
  $reportObject.powerpoint_version = [string]$app.Version

  $presentation = $app.Presentations.Open($pptxPath, -1, 0, -1)

  $slideWidthPt = [double]$presentation.PageSetup.SlideWidth
  $slideHeightPt = [double]$presentation.PageSetup.SlideHeight
  $pxW = [Math]::Max(1, [int][Math]::Round($slideWidthPt / 72.0 * $Dpi))
  $pxH = [Math]::Max(1, [int][Math]::Round($slideHeightPt / 72.0 * $Dpi))
  $slideCount = [int]$presentation.Slides.Count

  $slides = @()
  for ($i = 1; $i -le $slideCount; $i++) {
    $target = Join-Path $outPath ("slide_{0:D2}.png" -f $i)
    $slide = $presentation.Slides.Item($i)
    $slide.Export($target, "PNG", $pxW, $pxH)
    $item = Get-Item -LiteralPath $target -ErrorAction SilentlyContinue
    $slides += [ordered]@{
      index = $i
      png = $target
      exists = [bool]$item
      bytes = if ($item) { [int64]$item.Length } else { 0 }
    }
    Release-ComObject $slide
  }

  $rendered = @($slides | Where-Object { $_.exists -and $_.bytes -gt 0 })
  $reportObject.status = if ($rendered.Count -gt 0) { "rendered" } else { "error" }
  if ($rendered.Count -eq 0) {
    $reportObject.error = "PowerPoint reported success but no PNG was written."
  }
  $reportObject.slide_px = @($pxW, $pxH)
  $reportObject.slide_count = $slideCount
  $reportObject.slides = $slides
} catch {
  $reportObject.status = "error"
  $reportObject.error = $_.Exception.Message
} finally {
  if ($null -ne $presentation) {
    try { $presentation.Close() } catch {}
  }
  if ($null -ne $app) {
    try { $app.Quit() } catch {}
  }
  Release-ComObject $presentation
  Release-ComObject $app
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
}

$json = $reportObject | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($rawReportPath, $json + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
$json

if ($reportObject.status -eq "rendered") {
  exit 0
}
exit 1
