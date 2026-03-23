from __future__ import annotations

from docling_core.types.doc import DocItemLabel, DoclingDocument, TableCell, TableData

from extraction.models import ExtractionResult


def _build_table_data(rows: list[list[str]]) -> TableData:
    """Convert a list-of-lists into a TableData with TableCell objects."""
    if not rows:
        return TableData(table_cells=[], num_rows=0, num_cols=0)

    num_rows = len(rows)
    num_cols = max(len(row) for row in rows) if rows else 0

    cells: list[TableCell] = []
    for row_idx, row in enumerate(rows):
        for col_idx, text in enumerate(row):
            cells.append(
                TableCell(
                    text=text,
                    start_row_offset_idx=row_idx,
                    end_row_offset_idx=row_idx + 1,
                    start_col_offset_idx=col_idx,
                    end_col_offset_idx=col_idx + 1,
                    column_header=(row_idx == 0),
                )
            )

    return TableData(table_cells=cells, num_rows=num_rows, num_cols=num_cols)


def build_docling_document(
    result: ExtractionResult,
    source_filename: str,
) -> DoclingDocument:
    """Map an ExtractionResult to a DoclingDocument."""
    doc = DoclingDocument(name=source_filename)

    for element in result.elements:
        if element.type == "heading":
            doc.add_heading(
                text=element.text,
                level=element.level or 1,
            )

        elif element.type == "paragraph":
            doc.add_text(
                label=DocItemLabel.PARAGRAPH,
                text=element.text,
            )

        elif element.type == "list_item":
            doc.add_list_item(text=element.text)

        elif element.type == "table":
            table_data = _build_table_data(element.rows or [])
            doc.add_table(data=table_data)

        elif element.type == "image_description":
            doc.add_text(
                label=DocItemLabel.PARAGRAPH,
                text=element.text,
            )

        elif element.type == "redacted":
            doc.add_text(
                label=DocItemLabel.PARAGRAPH,
                text="[REDACTED]",
            )

    return doc
