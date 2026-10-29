from __future__ import annotations

from margin_db.source_ingestion import SourceIngestionError
from margin_ingest.contracts import ConversionResult
from margin_ingest.indexing import construct_normalized_document


class TestTextDocumentConverter:
    name = "test-text-converter"
    version = "test"

    def convert(self, *, filename: str, media_type: str, data: bytes) -> ConversionResult:
        text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            raise SourceIngestionError(f"{filename} did not yield extractable test text")
        return ConversionResult(
            normalized_document=construct_normalized_document(
                text,
                source_name=filename,
                format=media_type,
                metadata={
                    "filename": filename,
                    "media_type": media_type,
                    "byte_length": len(data),
                    "converter": self.name,
                    "converter_version": self.version,
                },
            ),
            converter=self.name,
            converter_version=self.version,
        )
