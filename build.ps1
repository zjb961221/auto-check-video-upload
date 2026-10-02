$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
& .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name VideoUploadCheck app.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Copy-Item queries.json dist\queries.json -Force
Copy-Item api_requests.json dist\api_requests.json -Force
Copy-Item updates.json dist\updates.json -Force
Copy-Item updates.example.json dist\updates.example.json -Force
Copy-Item README.md dist\README.md -Force
Write-Host 'Done: distribute VideoUploadCheck.exe together with queries.json from dist.'
