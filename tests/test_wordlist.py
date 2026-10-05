"""Custom word list (no models needed).
Run:  .venv\\Scripts\\python.exe -m unittest tests.test_wordlist -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wordlist


class Parsing(unittest.TestCase):
    def test_text_roundtrip(self):
        entries = wordlist.parse_text(
            "# comment\nKenyo | Kenyon, Kenya | 肯尤\nkanban | | 看板\n\nSOTA\n")
        self.assertEqual([e["word"] for e in entries], ["Kenyo", "kanban", "SOTA"])
        self.assertEqual(entries[0]["heard"], ["Kenyon", "Kenya"])
        self.assertEqual(entries[1]["translation"], "看板")
        again = wordlist.parse_text(wordlist.to_text(entries))
        self.assertEqual(again, entries)

    def test_clean_drops_empty_and_duplicates(self):
        out = wordlist.clean_entries([{"word": " "}, {"word": "A"}, {"word": "a"}, "junk", None])
        self.assertEqual([e["word"] for e in out], ["A"])

    def test_heard_may_be_a_comma_string_including_cjk_commas(self):
        out = wordlist.clean_entries([{"word": "Kenyo", "heard": "Kenyon，Kenya"}])
        self.assertEqual(out[0]["heard"], ["Kenyon", "Kenya"])


class Correct(unittest.TestCase):
    def wl(self, *rows):
        return wordlist.WordList([dict(word=w, heard=h, translation=t) for w, h, t in rows])

    def test_alias_is_replaced_whole_word_case_insensitive(self):
        wl = self.wl(("Kenyo", ["Kenyon", "can you"], ""))
        self.assertEqual(wl.correct("Ask kenyon about it. Kenyons car."), "Ask Kenyo about it. Kenyons car.")

    def test_close_spelling_of_a_long_word_is_snapped(self):
        wl = self.wl(("Charlotte", [], ""))
        self.assertEqual(wl.correct("I met Charlote yesterday."), "I met Charlotte yesterday.")

    def test_short_words_and_different_first_letter_are_left_alone(self):
        wl = self.wl(("Mark", [], ""), ("Charlotte", [], ""))
        self.assertEqual(wl.correct("Marks and sparks, harlotte."), "Marks and sparks, harlotte.")

    def test_real_different_words_two_letters_away_are_not_touched_for_medium_words(self):
        wl = self.wl(("Harvey", [], ""))
        self.assertEqual(wl.correct("We should harvest it."), "We should harvest it.")

    def test_multi_word_term(self):
        wl = self.wl(("Acme Dynamics", [], ""))
        self.assertEqual(wl.correct("I work at Acme Dynamic today."), "I work at Acme Dynamics today.")

    def test_cjk_alias(self):
        wl = self.wl(("肯尤", ["肯優", "垦尤"], ""))
        self.assertEqual(wl.correct("我認識肯優先生"), "我認識肯尤先生")

    def test_empty_list_changes_nothing(self):
        self.assertEqual(wordlist.WordList().correct("Anything here."), "Anything here.")

    def test_replacing_the_list_takes_effect(self):
        wl = wordlist.WordList()
        wl.set_entries([{"word": "Kenyo", "heard": ["Kenyon"]}])
        self.assertEqual(wl.correct("hi Kenyon"), "hi Kenyo")
        self.assertEqual(len(wl), 1)


class Hints(unittest.TestCase):
    def test_only_entries_present_are_returned_and_default_to_keep_as_is(self):
        wl = wordlist.WordList([
            {"word": "kanban", "translation": "看板"}, {"word": "Lumora"}, {"word": "sprint", "translation": "衝刺"}])
        self.assertEqual(wl.hints("Our Lumora board is a Kanban board."),
                         [("kanban", "看板"), ("Lumora", "Lumora")])
        self.assertEqual(wl.hints("Nothing relevant."), [])

    def test_word_boundaries(self):
        wl = wordlist.WordList([{"word": "art", "translation": "藝術"}])
        self.assertEqual(wl.hints("A smart start."), [])
        self.assertEqual(wl.hints("Modern art."), [("art", "藝術")])


class EditDistance(unittest.TestCase):
    def test_distance(self):
        self.assertEqual(wordlist.edit_distance("kitten", "sitting", 5), 3)
        self.assertEqual(wordlist.edit_distance("same", "same", 2), 0)
        self.assertGreater(wordlist.edit_distance("short", "muchlongerword", 2), 2)


if __name__ == "__main__":
    unittest.main()
