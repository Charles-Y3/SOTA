"""Excel import/export and the template of the word list (no models needed).
Run:  .venv\\Scripts\\python.exe -m unittest tests.test_wordlist_xlsx -v
"""
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wordlist
import wordlist_xlsx as wx

HEADER = ("Word or name", "Often heard as", "Translate as")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "list.xlsx")

    def tearDown(self):
        self.dir.cleanup()


class RoundTrip(Base):
    def test_entries_survive_writing_and_reading(self):
        entries = [
            {"word": "Acme Dynamics", "heard": ["Acne Dynamics", "Acme Dinamics"], "translation": "艾克米動力"},
            {"word": "R&D <team>", "heard": ["are and D"], "translation": 'say "R&D"'},
            {"word": "kanban", "heard": [], "translation": ""},
            {"word": "Q3", "heard": ["queue three"], "translation": "第三季"},
        ]
        wx.write_xlsx(self.path, entries, HEADER)
        self.assertEqual(wx.read_xlsx(self.path), wordlist.clean_entries(entries))

    def test_the_file_is_a_valid_zip_with_the_parts_excel_expects(self):
        wx.write_xlsx(self.path, [{"word": "a"}], HEADER)
        with zipfile.ZipFile(self.path) as z:
            names = set(z.namelist())
        for part in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
                     "xl/styles.xml", "xl/worksheets/sheet1.xml"):
            self.assertIn(part, names)

    def test_an_empty_list_gives_a_file_with_only_the_header(self):
        wx.write_xlsx(self.path, [], HEADER)
        self.assertEqual(wx.read_xlsx(self.path), [])

    def test_control_characters_cannot_corrupt_the_file(self):
        wx.write_xlsx(self.path, [{"word": "bad\x00\x08word", "heard": [], "translation": ""}], HEADER)
        self.assertEqual(wx.read_xlsx(self.path)[0]["word"], "badword")


class Template(Base):
    def test_the_template_is_empty_but_has_examples_on_a_second_sheet(self):
        wx.write_xlsx(self.path, [], HEADER, examples=wx.EXAMPLE_ENTRIES, tips=["Tip one", "Tip two"],
                      sheet_names=("Word list", "Examples and tips"))
        self.assertEqual(wx.read_xlsx(self.path), [])          # importing it unchanged adds nothing
        with zipfile.ZipFile(self.path) as z:
            second = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
            workbook = z.read("xl/workbook.xml").decode("utf-8")
        self.assertIn("Acme Dynamics", second)
        self.assertIn("Tip two", second)
        self.assertIn("Examples and tips", workbook)

    def test_examples_do_not_use_the_old_names(self):
        text = " ".join(e["word"] + " " + " ".join(e["heard"]) for e in wx.EXAMPLE_ENTRIES)
        self.assertNotIn("Kenyo", text)
        self.assertGreaterEqual(len(wx.EXAMPLE_ENTRIES), 6)
        # a mix: some with a required translation, some kept as written
        self.assertTrue(any(e["translation"] for e in wx.EXAMPLE_ENTRIES))
        self.assertTrue(any(not e["translation"] for e in wx.EXAMPLE_ENTRIES))


class FilesFromOtherPrograms(Base):
    """Excel/LibreOffice/Google Sheets store text in a shared-strings table."""

    def _excel_style_file(self, header_row=True):
        strings = ["Word or name", "Often heard as", "Translate as", "Lumora", "Lumera, Lumara", "OKR", "看板", "kanban"]
        sst = ('<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        rows = []
        if header_row:
            rows.append('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>')
        base = len(rows)
        rows.append(f'<row r="{base + 1}"><c r="A{base + 1}" t="s"><v>3</v></c><c r="B{base + 1}" t="s"><v>4</v></c></row>')
        rows.append(f'<row r="{base + 2}"><c r="A{base + 2}" t="s"><v>5</v></c></row>')       # B and C missing
        rows.append(f'<row r="{base + 3}"><c r="A{base + 3}" t="s"><v>7</v></c><c r="C{base + 3}" t="s"><v>6</v></c></row>')  # B skipped
        rows.append(f'<row r="{base + 4}"><c r="A{base + 4}"><v>2024</v></c></row>')           # a number cell
        sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 "<sheetData>" + "".join(rows) + "</sheetData></worksheet>")
        wb = ('<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
              'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
              '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId7"/></sheets></workbook>')
        rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                'Target="worksheets/sheet1.xml"/></Relationships>')
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("xl/workbook.xml", wb)
            z.writestr("xl/_rels/workbook.xml.rels", rels)
            z.writestr("xl/sharedStrings.xml", sst)
            z.writestr("xl/worksheets/sheet1.xml", sheet)

    def test_shared_strings_gaps_numbers_and_a_header(self):
        self._excel_style_file()
        entries = wx.read_xlsx(self.path)
        self.assertEqual([e["word"] for e in entries], ["Lumora", "OKR", "kanban", "2024"])
        self.assertEqual(entries[0]["heard"], ["Lumera", "Lumara"])
        self.assertEqual(entries[2]["translation"], "看板")

    def test_a_file_without_a_header_row_keeps_its_first_row(self):
        self._excel_style_file(header_row=False)
        self.assertEqual(wx.read_xlsx(self.path)[0]["word"], "Lumora")


class BadFiles(Base):
    def test_a_text_file_with_an_xlsx_name_is_refused_clearly(self):
        with open(self.path, "w") as f:
            f.write("just some text")
        with self.assertRaises(ValueError):
            wx.read_xlsx(self.path)

    def test_a_zip_that_is_not_a_workbook_is_refused(self):
        with zipfile.ZipFile(self.path, "w") as z:
            z.writestr("hello.txt", "x")
        with self.assertRaises(ValueError):
            wx.read_xlsx(self.path)

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(ValueError):
            wx.read_xlsx(os.path.join(self.dir.name, "nope.xlsx"))


if __name__ == "__main__":
    unittest.main()
