# TrueDose AI — Hackathon Prototype

TrueDose AI is a local demo for medicine-code verification, explainable risk flags, and a scan activity dashboard. It uses fictional sample records only.

## Requirements
- Python 3.10 or newer
- A browser
- Internet is not required for the core app after dependencies are installed. Google Fonts are optional cosmetic styling.

## Run on Windows
1. Extract the ZIP file.
2. Open the `TrueDose_AI` folder in VS Code.
3. Open Terminal → New Terminal.
4. Create a virtual environment:
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
   If PowerShell blocks activation, use Command Prompt:
   ```bat
   .venv\Scripts\activate.bat
   ```
5. Install dependencies:
   ```powershell
   python -m pip install -r requirements.txt
   ```
6. Start the server:
   ```powershell
   python app.py
   ```
7. Open **http://127.0.0.1:5000** in your browser. Keep the terminal open while using the app.

## Run on macOS/Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```
Then open **http://127.0.0.1:5000**.

## Demo codes
- `TD-1001` — registered sample record
- `TD-1002` — registered sample record
- `TD-EXPIRED-01` — expired-batch scenario
- `TD-CLONE-1001` — simulated suspicious cloned-code scenario
- `TD-NOT-FOUND` — unknown code

Click a sample code on the dashboard or type it into the verification field. After each scan, the dashboard metrics and recent activity update. Use **QR tools** to generate/download a QR image or scan one with a supported browser camera.

## API checks
Open these URLs in your browser:
- `http://127.0.0.1:5000/api/health` — server/database health
- `http://127.0.0.1:5000/api/medicines` — sample registry
- `http://127.0.0.1:5000/api/stats` — dashboard metrics
- `http://127.0.0.1:5000/api/qr?code=TD-1001` — generated PNG QR code for a registered demo code

## Important limitations
- This is a hackathon prototype, not a production medicine-authentication system.
- Sample records are fictional and locally stored in SQLite.
- A matching code does not prove the physical medicine pack is genuine.
- The clone scenario is deliberately seeded demo data, not an ML model finding.
- QR images can be generated for fictional demo registry codes. Camera scanning uses the browser built-in `BarcodeDetector` API when supported (recent Chrome/Edge); other browsers can still use manual code entry.
- No real SMS, manufacturer integration, blockchain, or machine-learning model is implemented.
- Do not use this prototype to decide whether to take a real medicine.

## Troubleshooting
- `python is not recognized`: install Python 3.10+ and enable "Add Python to PATH", then reopen the terminal.
- `ModuleNotFoundError: No module named 'flask'`: activate the virtual environment and run `python -m pip install -r requirements.txt`.
- Browser says it cannot connect: ensure `python app.py` is still running and use the exact URL `http://127.0.0.1:5000`.
- Port 5000 is busy: change `port=5000` at the bottom of `app.py` to `port=5001`, then visit `http://127.0.0.1:5001`.
