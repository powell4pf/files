# NurturedChoiceProducts Sales System

A local frontend and backend sales management system for stock control, invoices, credit notes, customer statements, and monthly sales reports.

## Run

```powershell
& "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py
```

Open:

```text
http://localhost:8080/
```

For normal use, double-click `Start Sales System.bat`.

For automatic local startup, double-click `Install Auto Start.bat`. This creates a Windows scheduled task that starts the local backend when Windows starts and checks it every 5 minutes.

Default login:

```text
Username: admin
Password: admin123
```

## Notes

- Data is stored in `data/sales_system.db`.
- If `.env` contains `SUPABASE_URL` and `SUPABASE_ANON_KEY`, product/customer/sales records are stored in Supabase.
- Run `supabase_schema.sql` once in Supabase SQL Editor before using Supabase mode.
- Generated PDFs are stored in `generated/`.
- The document template uses `assets/letterhead.pdf` exactly as provided.
