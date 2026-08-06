$ErrorActionPreference = "Stop"

$lab = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
  -ArgumentList @("-m", "services.lab_provider.app.main", "--port", "8789") `
  -WorkingDirectory (Get-Location) `
  -WindowStyle Hidden -PassThru

try {
  .\.venv\Scripts\python.exe -m services.research_service.golden_matrix_cli `
    --config benchmarks/matrices/golden-matrix.json `
    --out benchmarks/results/golden-matrix-local.json
} finally {
  Stop-Process -Id $lab.Id -Force -ErrorAction SilentlyContinue
}
