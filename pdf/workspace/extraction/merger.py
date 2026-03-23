from __future__ import annotations

from extraction.models import ElementItem, ExtractionResult


def _looks_incomplete(text: str) -> bool:
    """Check if text looks like it was cut off mid-sentence."""
    stripped = text.rstrip()
    if not stripped:
        return False
    return stripped[-1] not in ".!?:;\"')"


def _looks_like_continuation(text: str) -> bool:
    """Check if text looks like it continues from a previous element."""
    stripped = text.lstrip()
    if not stripped:
        return False
    return stripped[0].islower()


def merge_extraction_results(results: list[ExtractionResult]) -> ExtractionResult:
    if not results:
        return ExtractionResult(metadata={}, elements=[])
    if len(results) == 1:
        return results[0]

    metadata = results[0].metadata
    merged_elements: list[ElementItem] = []

    for result in results:
        for el in result.elements:
            if (
                merged_elements
                and merged_elements[-1].type == "paragraph"
                and el.type == "paragraph"
                and _looks_incomplete(merged_elements[-1].text)
                and _looks_like_continuation(el.text)
            ):
                prev = merged_elements[-1]
                merged_elements[-1] = ElementItem(
                    type="paragraph",
                    text=prev.text.rstrip() + " " + el.text.lstrip(),
                    page=prev.page,
                    page_end=el.page_end or el.page,
                )
            elif (
                merged_elements
                and merged_elements[-1].type == "table"
                and el.type == "table"
                and merged_elements[-1].rows
                and el.rows
                and len(merged_elements[-1].rows[0]) == len(el.rows[0])
            ):
                prev = merged_elements[-1]
                new_rows = el.rows
                if prev.rows[0] == el.rows[0]:
                    new_rows = el.rows[1:]
                merged_elements[-1] = ElementItem(
                    type="table",
                    text=prev.text,
                    page=prev.page,
                    page_end=el.page_end or el.page,
                    caption=prev.caption,
                    rows=prev.rows + new_rows,
                )
            else:
                merged_elements.append(el)

    return ExtractionResult(metadata=metadata, elements=merged_elements)
