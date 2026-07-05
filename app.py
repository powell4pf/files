from __future__ import annotations

import copy
import contextlib
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DATA_DIR = ROOT / "data"
GENERATED_DIR = ROOT / "generated"
ASSETS_DIR = ROOT / "assets"
LETTERHEAD_PATH = ASSETS_DIR / "letterhead.pdf"
COMPANY_ADDRESS = "P.O Box 8415-00200 Nairobi, Kenya"
SESSIONS: set[str] = set()


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()


class SupabaseStore:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.key = key.strip()
        self.base = f"{self.url}/rest/v1"

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.key)

    def request(self, method: str, table: str, params: dict | None = None, body: dict | list | None = None, prefer: str = "return=representation") -> list[dict]:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        endpoint = f"{self.base}/{table}"
        if query:
            endpoint = f"{endpoint}?{query}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": prefer,
        }
        req = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else []
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Supabase {exc.code}: {detail}") from exc

    def select(self, table: str, **params) -> list[dict]:
        return self.request("GET", table, params={"select": "*", **params})

    def insert(self, table: str, row: dict) -> dict:
        return self.request("POST", table, body=row)[0]

    def update(self, table: str, row: dict, **filters) -> list[dict]:
        return self.request("PATCH", table, params=filters, body=row)


SUPABASE = SupabaseStore(os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_ANON_KEY", ""))


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    GENERATED_DIR.mkdir(exist_ok=True)
    ASSETS_DIR.mkdir(exist_ok=True)


def money(value: float) -> str:
    return f"KES {value:,.2f}"


def next_supabase_number(table: str, prefix: str, date_value: str) -> str:
    compact_month = date_value[:7].replace("-", "")
    rows = SUPABASE.select(table, number=f"like.{prefix}-{compact_month}-%")
    return f"{prefix}-{compact_month}-{len(rows) + 1:04d}"


def month_bounds(month: str) -> tuple[str, str]:
    start = datetime.strptime(month, "%Y-%m").date()
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    end = next_month - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def year_bounds(year: str) -> tuple[str, str]:
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    return start, end


def maybe_seed_supabase() -> None:
    if not SUPABASE.enabled:
        return
    try:
        # Seed admin user
        users = SUPABASE.select("users", username="eq.admin")
        if not users:
            salt = secrets.token_hex(12)
            SUPABASE.insert("users", {
                "username": "admin",
                "salt": salt,
                "password_hash": hash_password("admin123", salt),
                "created_at": now_iso()
            })

        products = SUPABASE.select("products", limit="1")
        if products:
            return
        for name, sku, price, stock in [
            ("Quick Health Honey", "QHH-001", 550.0, 120),
            ("Sweetnut Peanut Butter", "SPB-001", 380.0, 90),
            ("Sweetnut Roasted Nuts", "SRN-001", 300.0, 150),
        ]:
            SUPABASE.insert(
                "products",
                {
                    "name": name,
                    "sku": sku,
                    "price": price,
                    "stock": stock,
                    "active": True,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                },
            )
    except Exception:
        pass


def calculate_supabase_items(raw_items: list[dict], adjust_stock: bool = False) -> tuple[list[dict], float]:
    items = []
    subtotal = 0.0
    for raw in raw_items:
        rows = SUPABASE.select("products", id=f"eq.{int(raw['product_id'])}", limit="1")
        product = rows[0] if rows else None
        if not product:
            raise ValueError("One selected product no longer exists.")
        qty = int(raw.get("quantity", 1))
        if qty <= 0:
            raise ValueError("Quantity must be greater than zero.")
        price = float(raw.get("price") or product["price"])
        line_total = qty * price
        subtotal += line_total
        items.append(
            {
                "product_id": product["id"],
                "name": product["name"],
                "sku": product["sku"],
                "quantity": qty,
                "price": price,
                "total": line_total,
            }
        )
        if adjust_stock:
            if int(product["stock"]) < qty:
                raise ValueError(f"Not enough stock for {product['name']}.")
            SUPABASE.update("products", {"stock": int(product["stock"]) - qty, "updated_at": now_iso()}, id=f"eq.{product['id']}")
    return items, subtotal


def patch_letterhead_page() -> BytesIO:
    packet = BytesIO()
    c = canvas.Canvas(packet, pagesize=A4)
    c.setFillColor(colors.white)
    c.rect(228, 716, 188, 22, stroke=0, fill=1)
    c.setFillColor(colors.HexColor("#111111"))
    c.setFont("Helvetica", 11)
    c.drawCentredString(322, 724, COMPANY_ADDRESS)
    c.save()
    packet.seek(0)
    return packet


def save_on_letterhead(body_pdf: BytesIO, output_path: Path) -> None:
    if not LETTERHEAD_PATH.exists():
        raise FileNotFoundError(f"Missing letterhead template: {LETTERHEAD_PATH}")

    letterhead = PdfReader(str(LETTERHEAD_PATH))
    body_reader = PdfReader(body_pdf)
    writer = PdfWriter()

    for body_page in body_reader.pages:
        page = copy.copy(letterhead.pages[0])
        page.merge_page(body_page)
        writer.add_page(page)

    with output_path.open("wb") as handle:
        writer.write(handle)


def build_document_pdf(title: str, meta: list[tuple[str, str]], rows: list[list[str]], totals: list[tuple[str, str]], output_path: Path) -> None:
    packet = BytesIO()
    doc = SimpleDocTemplate(
        packet,
        pagesize=A4,
        leftMargin=24 * mm,
        rightMargin=24 * mm,
        topMargin=61 * mm,
        bottomMargin=22 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#12302b"),
        alignment=1,
        spaceAfter=10,
    )
    normal = styles["BodyText"]
    normal.fontName = "Helvetica"
    normal.fontSize = 9
    normal.leading = 12
    cell_style = ParagraphStyle(
        "Cell",
        parent=normal,
        fontSize=7.6,
        leading=9.2,
        wordWrap="CJK",
    )
    header_style = ParagraphStyle(
        "HeaderCell",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    amount_style = ParagraphStyle(
        "AmountCell",
        parent=cell_style,
        alignment=2,
    )

    story = [Paragraph(title, title_style)]
    meta_rows = [[Paragraph(f"<b>{escape(str(label))}</b>", normal), Paragraph(escape(str(value or "-")), normal)] for label, value in meta]
    meta_table = Table(meta_rows, colWidths=[38 * mm, 100 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#33534d")),
            ]
        )
    )
    story.extend([meta_table, Spacer(1, 8)])

    def pdf_cell(value: object, style: ParagraphStyle = cell_style) -> Paragraph:
        return Paragraph(escape(str(value or "-")), style)

    header = rows[0] if rows else []
    if header == ["Document", "Date", "Customer", "Amount"]:
        col_widths = [38 * mm, 27 * mm, 63 * mm, 34 * mm]
    elif header == ["Invoice", "Date", "Status", "Amount"]:
        col_widths = [42 * mm, 28 * mm, 42 * mm, 34 * mm]
    else:
        col_widths = [64 * mm, 18 * mm, 36 * mm, 36 * mm]

    table_rows = []
    for row_index, row in enumerate(rows):
        formatted = []
        for col_index, value in enumerate(row):
            if row_index == 0:
                formatted.append(pdf_cell(value, header_style))
            elif col_index == len(row) - 1:
                formatted.append(pdf_cell(value, amount_style))
            else:
                formatted.append(pdf_cell(value))
        table_rows.append(formatted)

    table = Table(table_rows, repeatRows=1, colWidths=col_widths)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#12302b")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d7dedb")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6faf8")]),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([table, Spacer(1, 8)])

    if totals:
        total_rows = [[Paragraph(f"<b>{label}</b>", normal), Paragraph(value, normal)] for label, value in totals]
        total_table = Table(total_rows, colWidths=[95 * mm, 43 * mm], hAlign="RIGHT")
        total_table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("LINEABOVE", (0, -1), (-1, -1), 0.7, colors.HexColor("#12302b")),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(total_table)

    doc.build(story)
    packet.seek(0)
    save_on_letterhead(packet, output_path)


def parse_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def handle_supabase_get(handler: "SalesHandler", path: str) -> bool:
    if not SUPABASE.enabled:
        return False
    if path == "/api/health":
        handler.send_json({"ok": True, "database": "supabase"})
    elif path == "/api/dashboard":
        products = SUPABASE.select("products", active="eq.true")
        invoices = SUPABASE.select("invoices")
        customers = SUPABASE.select("customers", select="id")
        docs = SUPABASE.select("documents", order="id.desc", limit="8")

        # Calculate Top Customers
        customer_stats = {}
        for inv in (invoices or []):
            name = inv.get("customer_name", "Unknown")
            if name not in customer_stats:
                customer_stats[name] = {"customer": name, "invoices": 0, "total": 0.0}
            customer_stats[name]["invoices"] += 1
            customer_stats[name]["total"] += float(str(inv.get("total") or 0))
        
        top_customers = sorted(customer_stats.values(), key=lambda x: x["total"], reverse=True)[:5]

        handler.send_json(
            {
                "products": {"count": len(products), "stock": sum(int(row.get("stock") or 0) for row in products)},
                "revenue": sum(float(row.get("total") or 0) for row in invoices),
                "invoices": len(invoices),
                "customers": len(customers),
                "top_customers": top_customers,
                "documents": docs,
            }
        )
    elif path == "/api/products":
        handler.send_json(SUPABASE.select("products", order="active.desc,name.asc"))
    elif path == "/api/customers":
        handler.send_json(SUPABASE.select("customers", order="name.asc"))
    elif path == "/api/invoices":
        rows = SUPABASE.select("invoices", select="id,number,customer_name,invoice_date,status,total,pdf_path", order="id.desc")
        handler.send_json(rows)
    elif path == "/api/credit-notes":
        rows = SUPABASE.select("credit_notes", select="id,number,customer_name,credit_date,total,pdf_path", order="id.desc")
        handler.send_json(rows)
    elif path == "/api/documents":
        handler.send_json(SUPABASE.select("documents", order="id.desc"))
    else:
        return False
    return True


def handle_supabase_post(handler: "SalesHandler", path: str, payload: dict) -> bool:
    if not SUPABASE.enabled:
        return False
    if path == "/api/products":
        product_id = payload.get("id")
        row = {
            "name": payload["name"].strip(),
            "sku": payload["sku"].strip(),
            "price": float(payload["price"]),
            "stock": int(payload["stock"]),
            "active": bool(payload.get("active", True)),
            "updated_at": now_iso(),
        }
        if product_id:
            SUPABASE.update("products", row, id=f"eq.{product_id}")
        else:
            row["created_at"] = now_iso()
            SUPABASE.insert("products", row)
        handler.send_json({"ok": True})
        return True
    elif path == "/api/customers":
        row = SUPABASE.insert(
            "customers",
            {
                "name": payload["name"].strip(),
                "phone": payload.get("phone", "").strip(),
                "email": payload.get("email", "").strip(),
                "address": payload.get("address", "").strip(),
                "created_at": now_iso(),
            },
        )
        handler.send_json({"id": row["id"]})
        return True
    elif path == "/api/invoices":
        inv_id = payload.get("id")
        if inv_id:
            # Revert old stock before applying new changes
            old_rows = SUPABASE.select("invoices", id=f"eq.{inv_id}")
            if old_rows:
                old_items = old_rows[0].get("items_json", [])
                for oi in old_items:
                    p_rows = SUPABASE.select("products", id=f"eq.{oi['product_id']}")
                    if p_rows:
                        SUPABASE.update("products", {"stock": int(p_rows[0]["stock"]) + int(oi["quantity"])}, id=f"eq.{oi['product_id']}")

        invoice_date = payload.get("invoice_date") or date.today().isoformat()
        items, subtotal = calculate_supabase_items(payload.get("items", []), adjust_stock=True)

        number = payload.get("number")
        if number and not inv_id:
            existing_invoice = SUPABASE.select("invoices", number=f"eq.{number}")
            if existing_invoice:
                raise ValueError(f"Invoice number '{number}' already exists.")
        elif not number:
            number = next_supabase_number("invoices", "INV", invoice_date)

        VAT = float(payload.get("VAT") or 0)
        total = subtotal + VAT
        customer_name = payload.get("customer_name", "").strip() or "Walk-in Customer"
        output = GENERATED_DIR / f"{number}.pdf"
        rows = [["Product", "Qty", "Unit Price", "Line Total"]]
        rows += [[item["name"], str(item["quantity"]), money(item["price"]), money(item["total"])] for item in items]
        
        meta = [
            ("Invoice No.", str(number)),
            ("LPO No.", str(payload.get("lpo_number") or "-")),
            ("Customer", str(customer_name)),
            ("Invoice Date", str(invoice_date)),
            ("Due Date", str(payload.get("due_date") or "-")),
            ("Status", str(payload.get("status") or "Unpaid"))
        ]

        build_document_pdf(
            "Invoice",
            meta,
            rows,
            [("Subtotal", money(subtotal)), ("VAT", money(VAT)), ("Total", money(total))],
            output,
        )
        pdf_path = f"/generated/{output.name}"
        
        inv_data = {
                "number": str(number),
                "lpo_number": payload.get("lpo_number", ""),
                "customer_id": payload.get("customer_id"),
                "customer_name": customer_name,
                "invoice_date": invoice_date,
                "due_date": payload.get("due_date", ""),
                "status": payload.get("status", "Unpaid"),
                "notes": payload.get("notes", ""),
                "items_json": items,
                "subtotal": subtotal,
                "VAT": VAT,
                "total": total,
                "pdf_path": pdf_path,
                "created_at": now_iso(),
        }
        
        if inv_id:
            SUPABASE.update("invoices", inv_data, id=f"eq.{inv_id}")
        else:
            SUPABASE.insert("invoices", inv_data)
            
        SUPABASE.insert("documents", {"type": "invoice", "number": number, "title": customer_name, "pdf_path": pdf_path, "created_at": now_iso()})
        handler.send_json({"number": number, "pdf_path": pdf_path})
        return True
    elif path == "/api/credit-notes":
        cn_id = payload.get("id")
        credit_date = payload.get("credit_date") or date.today().isoformat()
        invoice = None
        if payload.get("invoice_id"):
            rows = SUPABASE.select("invoices", id=f"eq.{payload['invoice_id']}", limit="1")
            invoice = rows[0] if rows else None
            
        if cn_id and payload.get("restock"):
             # Revert stock from old credit note before applying new restock
             old_cn = SUPABASE.select("credit_notes", id=f"eq.{cn_id}")
             if old_cn:
                 for oi in old_cn[0].get("items_json", []):
                     p_rows = SUPABASE.select("products", id=f"eq.{oi['product_id']}")
                     if p_rows:
                         SUPABASE.update("products", {"stock": int(p_rows[0]["stock"]) - int(oi["quantity"])}, id=f"eq.{oi['product_id']}")

        number = payload.get("number")
        if number and not cn_id:
            existing = SUPABASE.select("credit_notes", number=f"eq.{number}")
            if existing:
                raise ValueError(f"Credit note number '{number}' already exists.")
        elif not number:
            number = next_supabase_number("credit_notes", "CN", credit_date)

        if invoice and not payload.get("items"):
            items = invoice["items_json"]
            total = float(invoice["total"])
            customer_name = invoice["customer_name"]
        else:
            items, total = calculate_supabase_items(payload.get("items", []), adjust_stock=False)
            customer_name = payload.get("customer_name", "").strip() or (invoice["customer_name"] if invoice else "Customer")

        if payload.get("restock"):
            for item in items:
                product = SUPABASE.select("products", id=f"eq.{item['product_id']}", limit="1")[0]
                SUPABASE.update("products", {"stock": int(product["stock"]) + int(item["quantity"]), "updated_at": now_iso()}, id=f"eq.{item['product_id']}")
        output = GENERATED_DIR / f"{number}.pdf"
        rows = [["Product", "Qty", "Unit Price", "Credit Total"]]
        rows += [[item["name"], str(item["quantity"]), money(item["price"]), money(item["total"])] for item in items]
        build_document_pdf("Credit Note", [("Credit Note No.", number), ("Reference Invoice", invoice["number"] if invoice else payload.get("reference", "")), ("Customer", customer_name), ("Date", credit_date), ("Reason", payload.get("reason", ""))], rows, [("Total Credit", money(total))], output)
        pdf_path = f"/generated/{output.name}"
        SUPABASE.insert("credit_notes", {"number": number, "invoice_id": payload.get("invoice_id"), "customer_name": customer_name, "credit_date": credit_date, "reason": payload.get("reason", ""), "items_json": items, "total": total, "pdf_path": pdf_path, "created_at": now_iso()})
        SUPABASE.insert("documents", {"type": "credit note", "number": number, "title": customer_name, "pdf_path": pdf_path, "created_at": now_iso()})
        handler.send_json({"number": number, "pdf_path": pdf_path})
        return True
    elif path.startswith("/api/invoices/") and path.endswith("/status"):
        # PATCH invoice payment status  e.g.  /api/invoices/42/status
        parts = path.split("/")
        try:
            inv_id = int(parts[3])
        except (IndexError, ValueError):
            raise ValueError("Invalid invoice id in URL.")
        new_status = payload.get("status", "").strip()
        if new_status not in ("Unpaid", "Paid", "Partially Paid", "Overdue"):
            raise ValueError("Status must be one of: Unpaid, Paid, Partially Paid, Overdue.")
        rows = SUPABASE.select("invoices", id=f"eq.{inv_id}")
        if not rows:
            raise ValueError("Invoice not found.")
        SUPABASE.update("invoices", {"status": new_status}, id=f"eq.{inv_id}")
        handler.send_json({"ok": True, "status": new_status})
        return True
    elif path == "/api/statements":
        customer_name = payload.get("customer_name", "").strip()
        rows_data = SUPABASE.select("invoices", customer_name=f"ilike.*{customer_name}*", order="invoice_date.asc")
        if not rows_data:
            raise ValueError("No invoices found for that customer.")
        number = f"ST-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        rows = [["Invoice", "Date", "Status", "Amount"]]
        rows += [[row["number"], row["invoice_date"], row["status"], money(row["total"])] for row in rows_data]
        total = sum(float(row["total"]) for row in rows_data)
        output = GENERATED_DIR / f"{number}.pdf"
        build_document_pdf("Customer Statement", [("Customer", customer_name), ("Generated", now_iso())], rows, [("Statement Total", money(total))], output)
        pdf_path = f"/generated/{output.name}"
        SUPABASE.insert("documents", {"type": "statement", "number": number, "title": customer_name, "pdf_path": pdf_path, "created_at": now_iso()})
        handler.send_json({"number": number, "pdf_path": pdf_path})
        return True
    elif path == "/api/reports/monthly":
        month = payload.get("month") or date.today().strftime("%Y-%m")
        start_date, end_date = month_bounds(month)
        invoices = SUPABASE.select("invoices", invoice_date=[f"gte.{start_date}", f"lte.{end_date}"], order="invoice_date.asc")
        credits = SUPABASE.select("credit_notes", credit_date=[f"gte.{start_date}", f"lte.{end_date}"], order="credit_date.asc")
        rows = [["Document", "Date", "Customer", "Amount"]]
        for row in invoices:
            rows.append([row["number"], row["invoice_date"], row["customer_name"], money(row["total"])])
        for row in credits:
            rows.append([row["number"], row["credit_date"], row["customer_name"], f"-{money(row['total'])}"])
        sales_total = sum(float(row["total"]) for row in invoices)
        credit_total = sum(float(row["total"]) for row in credits)
        number = f"MSR-{month.replace('-', '')}"
        output = GENERATED_DIR / f"{number}.pdf"
        build_document_pdf("Monthly Sales Report", [("Month", month), ("Generated", now_iso())], rows, [("Gross Sales", money(sales_total)), ("Credits", money(credit_total)), ("Net Sales", money(sales_total - credit_total))], output)
        pdf_path = f"/generated/{output.name}"
        SUPABASE.insert("documents", {"type": "monthly report", "number": number, "title": month, "pdf_path": pdf_path, "created_at": now_iso()})
        handler.send_json({"number": number, "pdf_path": pdf_path})
        return True
    elif path == "/api/reports/annual":
        year = payload.get("year") or str(date.today().year)
        start_date, end_date = year_bounds(year)
        invoices = SUPABASE.select("invoices", invoice_date=[f"gte.{start_date}", f"lte.{end_date}"])
        credits = SUPABASE.select("credit_notes", credit_date=[f"gte.{start_date}", f"lte.{end_date}"])
        
        months_names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
        comparison_data = {i+1: {"month": months_names[i], "invoices": 0, "revenue": 0.0, "credits": 0.0} for i in range(12)}
        
        for inv in invoices:
            date_str = inv.get("invoice_date", "")
            if "-" in date_str:
                m = int(date_str.split("-")[1])
                comparison_data[m]["invoices"] += 1
                comparison_data[m]["revenue"] += float(str(inv.get("total") or 0))
            
        for cr in credits:
            date_str = cr.get("credit_date", "")
            if "-" in date_str:
                m = int(date_str.split("-")[1])
                comparison_data[m]["credits"] += float(str(cr.get("total") or 0))
            
        rows = [["Month", "Invoices", "Revenue", "Credit Notes", "Net"]]
        grand_revenue = 0.0
        grand_credits = 0.0
        
        for i in range(1, 13):
            d = comparison_data[i]
            net = d["revenue"] - d["credits"]
            rows.append([d["month"], str(d["invoices"]), money(d["revenue"]), money(d["credits"]), money(net)])
            grand_revenue += d["revenue"]
            grand_credits += d["credits"]

        number = f"ANR-{year}"
        output = GENERATED_DIR / f"{number}.pdf"
        build_document_pdf(
            f"{year} Annual Comparison", 
            [("Year", year), ("Generated", now_iso())], 
            rows, 
            [("Annual Revenue", money(grand_revenue)), ("Annual Credits", money(grand_credits)), ("Annual Net", money(grand_revenue - grand_credits))], 
            output
        )
        pdf_path = f"/generated/{output.name}"
        SUPABASE.insert("documents", {"type": "annual report", "number": number, "title": year, "pdf_path": pdf_path, "created_at": now_iso()})
        handler.send_json({"number": number, "pdf_path": pdf_path})
        return True
    else:
        return False
    return True


class SalesHandler(BaseHTTPRequestHandler):
    server_version = "NurturedChoiceSales/1.0"

    def log_message(self, format: str, *args) -> None:
        return

    def send_json(self, payload: dict | list, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if content_type == "application/pdf":
            self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(data)

    def require_auth(self) -> bool:
        if self.path.startswith("/api/login") or self.path.startswith("/api/health"):
            return True
        if not self.path.startswith("/api/"):
            return True
        token = self.headers.get("Authorization", "").replace("Bearer ", "")
        if token in SESSIONS:
            return True
        self.send_json({"error": "Please log in again."}, 401)
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/generated/"):
            self.send_file(ROOT / path.lstrip("/"), "application/pdf")
            return
        if not path.startswith("/api/"):
            file_path = WEB_ROOT / ("index.html" if path == "/" else path.lstrip("/"))
            content_type = "text/html; charset=utf-8"
            if file_path.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            elif file_path.suffix == ".js":
                content_type = "text/javascript; charset=utf-8"
            self.send_file(file_path, content_type)
            return
        if not self.require_auth():
            return
        try:
            if not handle_supabase_get(self, path):
                self.send_json({"error": "Unknown endpoint or Supabase disabled."}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = parse_json(self)
            if path == "/api/login":
                username = payload.get("username", "")
                password = payload.get("password", "")
                users = SUPABASE.select("users", username=f"eq.{username}")
                user = users[0] if users else None
                if user and hash_password(password, user["salt"]) == user["password_hash"]:
                    token = secrets.token_urlsafe(32)
                    SESSIONS.add(token)
                    self.send_json({"token": token, "user": {"username": username}})
                else:
                    self.send_json({"error": "Invalid username or password."}, 401)
                return
            if not self.require_auth():
                return

            if path == "/api/logout":
                token = self.headers.get("Authorization", "").replace("Bearer ", "")
                if token in SESSIONS:
                    SESSIONS.remove(token)
                self.send_json({"ok": True})
                return

            # PATCH-style status update routed via POST dispatcher
            if not handle_supabase_post(self, path, payload):
                self.send_json({"error": "Unknown endpoint or Supabase disabled."}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)


def main() -> None:
    init_db()
    maybe_seed_supabase()
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), SalesHandler)
    print(f"NurturedChoiceProducts Sales System running at http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
