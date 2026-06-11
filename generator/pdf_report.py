from pathlib import Path
from typing import List


class SimplePDFReport:
    """Genera PDFs sencillos de texto sin dependencias externas."""

    def __init__(self, output_path: str) -> None:
        self.output_path = Path(output_path)

    def write(self, lines: List[str]) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        pages = self._paginate(lines)
        objects: List[str] = []

        catalog_id = 1
        pages_id = 2
        font_id = 3
        next_id = 4
        page_ids = []
        content_ids = []

        for page_lines in pages:
            page_id = next_id
            content_id = next_id + 1
            next_id += 2
            page_ids.append(page_id)
            content_ids.append(content_id)

            stream = self._content_stream(page_lines)
            objects.append(
                f"{page_id} 0 obj\n"
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>\n"
                "endobj\n"
            )
            objects.append(
                f"{content_id} 0 obj\n"
                f"<< /Length {len(stream.encode('latin-1', errors='replace'))} >>\n"
                "stream\n"
                f"{stream}"
                "endstream\n"
                "endobj\n"
            )

        header_objects = [
            f"{catalog_id} 0 obj\n<< /Type /Catalog /Pages {pages_id} 0 R >>\nendobj\n",
            (
                f"{pages_id} 0 obj\n"
                f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
                f"/Count {len(page_ids)} >>\n"
                "endobj\n"
            ),
            f"{font_id} 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        ]

        all_objects = header_objects + objects
        offsets = []
        pdf = "%PDF-1.4\n"
        for obj in all_objects:
            offsets.append(len(pdf.encode("latin-1", errors="replace")))
            pdf += obj

        xref_offset = len(pdf.encode("latin-1", errors="replace"))
        pdf += f"xref\n0 {len(all_objects) + 1}\n"
        pdf += "0000000000 65535 f \n"
        for offset in offsets:
            pdf += f"{offset:010d} 00000 n \n"
        pdf += (
            "trailer\n"
            f"<< /Size {len(all_objects) + 1} /Root {catalog_id} 0 R >>\n"
            "startxref\n"
            f"{xref_offset}\n"
            "%%EOF\n"
        )

        self.output_path.write_bytes(pdf.encode("latin-1", errors="replace"))

    def _paginate(self, lines: List[str]) -> List[List[str]]:
        wrapped_lines: List[str] = []
        for line in lines:
            wrapped_lines.extend(self._wrap(line, 96))

        page_size = 58
        return [
            wrapped_lines[index : index + page_size]
            for index in range(0, len(wrapped_lines), page_size)
        ] or [[""]]

    def _wrap(self, line: str, width: int) -> List[str]:
        if not line:
            return [""]

        chunks = []
        current = line
        while len(current) > width:
            split_at = current.rfind(" ", 0, width)
            if split_at <= 0:
                split_at = width
            chunks.append(current[:split_at])
            current = current[split_at:].lstrip()
        chunks.append(current)
        return chunks

    def _content_stream(self, lines: List[str]) -> str:
        stream_lines = ["BT", "/F1 9 Tf", "50 760 Td", "12 TL"]
        for index, line in enumerate(lines):
            if index:
                stream_lines.append("T*")
            stream_lines.append(f"({self._escape(line)}) Tj")
        stream_lines.append("ET")
        return "\n".join(stream_lines) + "\n"

    def _escape(self, text: str) -> str:
        return (
            text.encode("latin-1", errors="replace")
            .decode("latin-1")
            .replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
