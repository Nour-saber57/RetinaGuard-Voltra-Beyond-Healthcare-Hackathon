# RetinaGuard frontend

The complete static frontend exported from the current RetinaGuard demo on 16 October 2026.

## Start locally

No npm packages or build step are required. This is plain HTML, CSS, and JavaScript in `index.html`, not a React project.

Start the backend first from the `Backend` folder:

```bash
python -m uvicorn main:app --reload --port 8001
```

From this folder, run:

```bash
python -m http.server 8000
```

On Windows, you can use `py -m http.server 8000` instead. Open http://localhost:8000 in your browser. The frontend calls `http://localhost:8001` by default. To use another API URL, define `window.RETINAGUARD_API_BASE` before the application script in `index.html`. Use localhost or HTTPS so location features can work. Location access still requires user permission.

## Add to your existing repository

1. Extract this ZIP.
2. Copy the `retinaguard-frontend` folder into your repository, for example under `frontend-demo/`. Keep an existing frontend intact unless you intend to replace it.
3. Commit and push that folder using your normal Git workflow. Alternatively, use GitHub's file upload interface and upload the extracted files, not just the ZIP.
4. To serve the UI, point your static host at the folder containing `index.html`.

This export does not change or upload to your repository automatically.

## Features

- Patient registration/sign-in backed by the API; clinic sign-in only.
- Role choice appears only on the authentication screens.
- Ordered patient pages with Back/Continue and a page-name selector.
- Interactive Learn & protect care cycle.
- Persistent weight, HbA1c, and blood pressure entries, each with its own measurement date.
- Health charts, date filters, editable measurement history, and diabetes type.
- Profile with contact information and personal charts.
- API-provided illustrative clinics, location sorting, available time slots, booking and cancellation.
- Five-stage care progress tracker.
- Clinic dashboard, patient records, screening image upload, and image preview.

## Clinic demo credentials

Email: `clinic@retinaguard.demo`
Password: `DemoClinic2026!`

These are intentionally public example credentials, not secrets or production authentication.

## Integration boundaries

- Patient accounts, contact details, diabetes type, health measurements, care milestones, appointments, and screening records are saved in the backend SQLite database. Sign in again after refreshing; access tokens are held in memory only.
- Patient endpoints are scoped to the signed-in patient. Clinic-wide endpoints require a clinic token. Clinic demo credentials are public examples and must be replaced for any deployment.
- Clinics, coordinates, fees, hours, and slot availability are illustrative. Bookings are database-backed demonstrations, not real clinic reservations. No messages, payment, or retinal AI analysis occur.
- Uploaded JPEG/PNG images are stored under `Backend/uploads`. The service does not analyze or diagnose images.
- Case confirmation email is disabled unless `SEND_CASE_EMAILS=true` is explicitly configured.
- Use example information only. This prototype has not had the security, privacy, clinical, or operational review needed for real patient records.
- SQLite tables are created at startup. The existing `cases` table receives a small nullable ownership-column migration; other future schema changes should use a migration tool before deployment.

## Source organization

The exported `index.html` matches the deployed frontend. CSS is in its `<style>` block and application logic in its `<script>` block. Features were added incrementally; some later function assignments extend earlier implementations. Preserve script order. There are no framework dependencies or bundled API keys.

No Sites hosting manifest, source credentials, Git history, environment files, or private server configuration is included.
