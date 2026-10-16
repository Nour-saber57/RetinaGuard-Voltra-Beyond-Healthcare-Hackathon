# RetinaGuard · Voltra × Beyond Healthcare Hackathon

RetinaGuard is a healthcare prototype for organizing patient eye-care follow-up. The frontend is a static HTML application; the FastAPI backend persists patient accounts, health measurements, care progress, appointments, and screening image records in SQLite.

## Run locally

1. Install dependencies with `pip install -r requirements.txt`.
2. Start the API from `Backend`: `python -m uvicorn main:app --reload --port 8001`.
3. Serve `Frontend` with `python -m http.server 8000` and open `http://localhost:8000`.

The API docs are available at `http://localhost:8001/docs`. The frontend defaults to that API URL. See [Frontend/README.md](Frontend/README.md) for integration details and prototype limitations.

Patient routes require patient bearer tokens. Clinic-wide routes require clinic access. The included clinic login is a public demo credential; configure replacement credentials and a stable `RETINAGUARD_SECRET_KEY` before deployment. Clinic data and appointment slots are illustrative, and uploaded retinal images are not analyzed or used for diagnosis.

This prototype is not approved for real patient data or clinical use.
