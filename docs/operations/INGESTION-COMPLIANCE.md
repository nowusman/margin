# Source Ingestion Compliance and Data Governance

Source ingestion records each uploaded document, immutable version, converted page, source element, conversion-quality report, parent ingestion job, and extraction outcome in PostgreSQL 18.4. Raw and converted artifacts are retained in object storage.

Margin enforces strict compliance standards ensuring that ingested architectural documentation is processed locally, tracked immutably, and governed with zero ungrounded data leakage.

## Table of contents

- [Approved ingestion boundary](#approved-ingestion-boundary)
- [Document conversion standards](#document-conversion-standards)
- [Recorded provenance and element accounting](#recorded-provenance-and-element-accounting)
- [Lifecycle accountability and failure handling](#lifecycle-accountability-and-failure-handling)
- [Data security and compliance rules](#data-security-and-compliance-rules)

## Approved ingestion boundary

The approved local ingestion boundary supports rich document conversion, lightweight markup parsing, and machine-readable contract extraction. It does not synchronize repositories, consume external catalogs, or run external connector credentials.

| Format Group | Supported File Types | Conversion Engine | Operational Boundary |
| --- | --- | --- | --- |
| Rich Documents | PDF, DOCX, PPTX | Docling Primary / MarkItDown Fallback | Local processing; layout and reading order extracted |
| Visual Assets | PNG, JPEG, TIFF, WebP | Pillow Image Converter / OCR Pipeline | Local image normalization and bounding box extraction |
| Structured Text | Markdown, YAML, JSON, TXT | Lightweight Built-in Converters | Dependency-free local string and structure parsing |
| Architecture Diagrams | Mermaid (`.mmd`), PlantUML (`.puml`), Structurizr (`.dsl`) | Native Diagram Converters | Text parsing and visual SVG extraction |
| Interface Contracts | OpenAPI (v2, v3, v3.1), AsyncAPI (v2, v3) | Native Contract Converters | Schema, endpoint, channel, and message modeling |
| Prohibited Operations | Repository sync, external crawling | None | Explicitly forbidden from core runtime |

## Document conversion standards

- **Deterministic output**: Given the same file, document converters produce deterministic layout pages and normalized element IDs.
- **Fail-open fallback**: When `DoclingPrimaryConverter` encounters unsupported formatting, processing cleanly falls back to `MarkItDownSecondaryConverter` with an audited warning.
- **Visual asset fidelity**: Embedded diagrams and architectural illustrations are captured with high-resolution coordinates and stored in S3 object storage for visual preview.

## Recorded provenance and element accounting

Each uploaded file produces:

- A `SourceDocument` record tracking cryptographic SHA-256 checksum, MIME type, category, sensitivity, and upload timestamp.
- A `SourceVersion` capturing the revision state of reprocessed documents.
- `SourcePage` records representing physical or logical document pages.
- `SourceElement` records capturing exact paragraphs, tables, diagram blocks, and code sections with bounding box coordinates.
- An `IngestionReceipt` detailing exact element counts, extracted IdeaBlocks, canonical entity bindings, and unextractable elements.

The retained artifacts and the records that describe them are kept strictly separated: object storage holds the raw file bytes, while PostgreSQL 18.4 holds the relational structure and provenance metadata.

## Lifecycle accountability and failure handling

An ingestion job moves through the documented unified lifecycle: `received` -> `converting` -> `extracting` -> `resolving` -> `embedding` -> `completed`.

- **No silent loss**: A file that is uploaded but yields no claim is recorded as an explicit outcome, never silently discarded.
- **Partial conversion reporting**: If a conversion fails partially (e.g. 2 of 20 pages contain unsupported fonts), the ingestion report records what was converted, what failed, and raises a review task for operator guidance.
- **Review isolation**: Ambiguous, contradictory, or quarantined elements are halted in review tasks without halting non-conflicting claims.

## Data security and compliance rules

- **Local-only execution**: All document parsers run inside the container boundary without transmitting document content to unauthorized third-party APIs.
- **Access scoping**: Extracted elements inherit the domain, category, and sensitivity boundaries of their parent source document.
- **Immutable audit**: Deletions and reprocessing runs generate tamper-evident audit logs recording the operator principal and justification.
