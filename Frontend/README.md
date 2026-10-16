# RetinaGuard frontend

The complete static frontend exported from the current RetinaGuard demo on 16 October 2026.

## Start locally

No npm packages or build step are required. This is plain HTML, CSS, and JavaScript in `index.html`, not a React project.

From this folder, run:

```bash
python -m http.server 8000
```

On Windows, you can use `py -m http.server 8000` instead. Open http://localhost:8000 in your browser. Use localhost or HTTPS so browser cryptography and location features can work. Location access still requires user permission.

## Add to your existing repository

1. Extract this ZIP.
2. Copy the `retinaguard-frontend` folder into your repository, for example under `frontend-demo/`. Keep an existing frontend intact unless you intend to replace it.
3. Commit and push that folder using your normal Git workflow. Alternatively, use GitHub's file upload interface and upload the extracted files, not just the ZIP.
4. To serve the UI, point your static host at the folder containing `index.html`.

This export does not change or upload to your repository automatically.

## Features

- Patient sign-up/sign-in demo; clinic sign-in only.
- Role choice appears only on the authentication screens.
- Ordered patient pages with Back/Continue and a page-name selector.
- Interactive Learn & protect care cycle.
- Weight, HbA1c, and blood pressure, each with its own measurement date.
- Health charts, date filters, editable measurement history, and diabetes type.
- Profile with contact information and personal charts.
- Illustrative nearby providers, location sorting, available time slots, booking and cancellation.
- Five-stage care progress tracker.
- Clinic dashboard, records, and image preview.

## Clinic demo credentials

Email: `clinic@retinaguard.demo`
Password: `DemoClinic2026!`

These are intentionally public example credentials, not secrets or production authentication.

## Current limitations

- This is a frontend prototype. Accounts, health entries, appointments, and progress are held in JavaScript memory and reset on refresh.
- Client-side role selection and password checking do not enforce secure access. There is no server-side authentication or authorization.
- Clinics, coordinates, schedules, and fees are examples. No real booking, message, payment, or retinal AI analysis occurs.
- Use example information only; do not deploy this as a real patient-record system.
- Connecting your FastAPI backend requires implementing server authentication, per-user/clinic authorization, persistent models, real availability, and API calls in place of the in-memory operations.
- Source syntax and targeted logic checks were performed during development. Visual browser verification was blocked by the authoring environment.

## Source organization

The exported `index.html` matches the deployed frontend. CSS is in its `<style>` block and application logic in its `<script>` block. Features were added incrementally; some later function assignments extend earlier implementations. Preserve script order. There are no framework dependencies or bundled API keys.

No Sites hosting manifest, source credentials, Git history, environment files, or private server configuration is included.
