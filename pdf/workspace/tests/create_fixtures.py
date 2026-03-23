import pikepdf
import pathlib


def create_simple_pdf(path, num_pages=1):
    """Create a minimal PDF with the given number of pages."""
    pdf = pikepdf.Pdf.new()
    for i in range(num_pages):
        page = pikepdf.Page(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Page,
                MediaBox=[0, 0, 612, 792],
            )
        )
        pdf.pages.append(page)
    pdf.save(path)


if __name__ == "__main__":
    fixtures = pathlib.Path(__file__).parent / "fixtures"
    fixtures.mkdir(exist_ok=True)
    create_simple_pdf(fixtures / "simple.pdf", 1)
    create_simple_pdf(fixtures / "multipage.pdf", 3)
    create_simple_pdf(fixtures / "large.pdf", 120)
    print("Fixtures created.")
