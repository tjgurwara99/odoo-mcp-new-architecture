# MCP Server - Report Designer

Domain add-on for **MCP Server (Core)** (`mcp_server`). Exposes the custom
reports built with the GTECH **Report Designer (XLSX, XLSM)** module
(`reports_designer`, model `reports.designer`) to MCP clients as short-lived,
downloadable **PDF** links.

## Tools

| Tool | Kind | Description |
| --- | --- | --- |
| `reports_designer.list_reports` | read | List the available Report Designer report definitions. |
| `reports_designer.get_report` | read | Full detail of one report incl. its parameters (code, type, required). |
| `reports_designer.generate_report` | read | Generate a report as PDF for records / parameter values and return a short-lived download link. |

Tool names reach MCP clients in wire-safe form (e.g. `reports_designer_list_reports`).

## How generation works

Report Designer reports are not QWeb templates — they are produced by a
dedicated engine that builds an XLSX workbook and (here) converts it to PDF via
LibreOffice. `reports_designer.generate_report`:

1. Maps the caller's `params` (keyed by parameter **code**) onto the report's
   dynamic wizard fields and enforces read access on any `record_ids` supplied.
2. Calls `reports_designer_gen.create_xls(...)` — as the authenticated user — to
   build the XLSX `ir.attachment`.
3. Converts it to PDF (`reports.scheduler.convert_excel_to_pdf`, LibreOffice)
   and stores the PDF as a fresh `ir.attachment`.
4. Mints a tokenized download URL for that PDF via the core
   `mcp.report.link.mint_attachment` facility.

## PDF report links

The returned `pdf_url` is produced by the core `mcp.report.link` facility using
its new **attachment-backed** link mode:

- The URL contains a high-entropy opaque token (only its SHA-256 hash is stored).
- The link streams a PDF that was generated **as the user who requested it** —
  no Odoo login required to open the link, but ACLs are enforced when the
  document is built and again (on the attachment) at download time.
- Links expire after `mcp_server.report_link_ttl` (default 1h) and are GC'd by
  cron.

> Requires **Public Base URL** to be set in *Settings > MCP Server* (used to
> build absolute link URLs) and **LibreOffice/soffice** to be installed on the
> Odoo server (for the XLSX → PDF conversion).

## Design

- Executes as the authenticated `res.users`; standard Report Designer and
  business-record ACLs / record rules apply. The report engine's own internal
  `sudo()` usage is unchanged (it belongs to the client's module).
- No HTTP is handled here — the core `mcp_server` module owns the transport and
  the `/mcp/report/<token>` download endpoint.

## Dependencies

`mcp_server`, `reports_designer`.
