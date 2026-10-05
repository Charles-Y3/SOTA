"""Timing rules of the rolling subtitles (no screen needed).
Run:  .venv\\Scripts\\python.exe -m unittest tests.test_subtitles -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subtitles


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Controller(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.c = subtitles.SubtitleController(self.clock)

    def test_nothing_until_something_happens(self):
        self.assertIsNone(self.c.view())

    def test_words_appear_live_while_being_spoken(self):
        self.c.on_preview("Interesting, how are")
        v = self.c.view()
        self.assertEqual(v["current"], ("Interesting, how are", ""))
        self.assertIsNone(v["previous"])
        self.c.on_preview("Interesting, how are you all today")
        v2 = self.c.view()
        self.assertEqual(v2["current"][0], "Interesting, how are you all today")
        self.assertEqual(v2["seq"], v["seq"])                  # the same sentence, only longer

    def test_the_live_words_become_the_finished_sentence_without_a_roll(self):
        self.c.on_preview("Hello there")
        seq = self.c.view()["seq"]
        self.c.on_preview("")                                  # the worker clears it on commit
        self.c.on_segment(1, "Hello there.", "", "pending")
        v = self.c.view()
        self.assertEqual(v["current"], ("Hello there.", ""))
        self.assertEqual(v["seq"], seq)
        self.c.on_segment(1, "Hello there.", "你", "streaming")
        self.assertEqual(self.c.view()["current"], ("Hello there.", "你"))      # shown as it is written
        self.c.on_segment(1, "Hello there.", "你好", "streaming")
        self.assertEqual(self.c.view()["current"], ("Hello there.", "你好"))
        self.c.on_segment(1, "Hello there.", "你好", "final")
        v = self.c.view()
        self.assertEqual(v["current"], ("Hello there.", "你好"))
        self.assertEqual(v["seq"], seq)

    def test_the_translation_of_finished_clauses_shows_under_the_live_words(self):
        self.c.on_preview("First, we need to finish the design, and then", "首先，我們")
        v = self.c.view()
        self.assertEqual(v["current"], ("First, we need to finish the design, and then", "首先，我們"))
        seq = v["seq"]
        self.c.on_preview("First, we need to finish the design, and then we", "首先，我們需要")
        self.assertEqual(self.c.view()["seq"], seq)            # still the same sentence
        # committed: the sentence keeps the translation it already had (no blank flash)
        self.c.on_segment(1, "First, we need to finish the design, and then we test.",
                          "首先，我們需要", "pending")
        v = self.c.view()
        self.assertEqual(v["current"][1], "首先，我們需要")
        self.assertEqual(v["seq"], seq)

    def test_a_new_sentence_rolls_the_old_one_up(self):
        self.c.on_preview("One")
        self.c.on_segment(1, "One.", "一", "final")
        first = self.c.view()
        self.clock.t += 1.0
        self.c.on_preview("Two is being spo")                  # the next sentence has started
        v = self.c.view()
        self.assertEqual(v["current"], ("Two is being spo", ""))
        self.assertEqual(v["previous"], ("One.", "一"))
        self.assertEqual(v["prev_seq"], first["seq"])
        self.assertNotEqual(v["seq"], first["seq"])

    def test_two_rolls_push_the_oldest_out(self):
        for n, word in enumerate(("One", "Two", "Three"), 1):
            self.c.on_preview(word)
            self.c.on_segment(n, f"{word}.", f"T{n}", "final")
            self.clock.t += 1.0
        v = self.c.view()
        self.assertEqual(v["current"][0], "Three.")
        self.assertEqual(v["previous"][0], "Two.")

    def test_two_sentences_committed_together_still_roll(self):
        self.c.on_preview("One. Two.")
        self.c.on_segment(1, "One.", "一", "final")
        self.c.on_segment(2, "Two.", "", "pending")
        v = self.c.view()
        self.assertEqual(v["current"][0], "Two.")
        self.assertEqual(v["previous"], ("One.", "一"))

    def test_lines_do_not_vanish_while_being_read_or_during_a_short_pause(self):
        self.c.on_segment(1, "One.", "一", "final")
        self.clock.t += 1.0
        self.c.on_segment(2, "Two.", "二", "final")
        hold = subtitles.SubtitleController.hold_s("二", "Two.")
        self.clock.t += hold + 1.0                              # silence after both were read
        v = self.c.view()
        self.assertEqual(v["current"][0], "Two.")
        self.assertEqual(v["previous"][0], "One.")              # still there

    def test_after_a_long_silence_the_older_lines_roll_away_one_by_one_then_the_last(self):
        for n in (1, 2, 3, 4):
            self.c.on_segment(n, f"S{n}.", f"T{n}", "final")
        hold = subtitles.SubtitleController.hold_s("T4", "S4.")
        self.clock.t += hold + 0.1
        self.assertEqual([h["src"] for h in self.c.view()["history"]], ["S1.", "S2.", "S3.", "S4."])
        self.clock.t += subtitles.TRIM_STEP_S                    # down to two
        self.assertEqual([h["src"] for h in self.c.view()["history"]], ["S3.", "S4."])
        self.clock.t += subtitles.TRIM_STEP_S                    # then one
        v = self.c.view()
        self.assertEqual([h["src"] for h in v["history"]], ["S4."])
        self.assertIsNone(v["previous"])
        self.clock.t += subtitles.CLEAR_AFTER_S                  # and finally the last one
        self.assertIsNone(self.c.view())

    def test_history_holds_the_last_four_sentences_oldest_first(self):
        for n in range(1, 7):
            self.c.on_segment(n, f"S{n}.", f"T{n}", "final")
        v = self.c.view()
        self.assertEqual([(h["src"], h["tr"]) for h in v["history"]],
                         [("S3.", "T3"), ("S4.", "T4"), ("S5.", "T5"), ("S6.", "T6")])
        self.assertEqual(v["current"], ("S6.", "T6"))
        self.assertEqual(v["previous"], ("S5.", "T5"))

    def test_history_includes_the_live_sentence_with_its_partial_translation(self):
        self.c.on_segment(1, "One.", "T1", "final")
        self.c.on_preview("Two is being", "二")
        h = self.c.view()["history"]
        self.assertEqual([(x["src"], x["tr"]) for x in h], [("One.", "T1"), ("Two is being", "二")])

    def test_hold_counts_from_when_the_translation_finished(self):
        self.c.on_segment(1, "Hi.", "", "pending")
        self.clock.t += 5.0
        self.assertIsNotNone(self.c.view())                    # still waiting for the translation
        self.c.on_segment(1, "Hi.", "嘿", "final")
        self.clock.t += subtitles.CLEAR_AFTER_S                # well inside hold + clear window
        self.assertEqual(self.c.view()["current"], ("Hi.", "嘿"))

    def test_an_untranslated_sentence_is_eventually_cleared(self):
        self.c.on_segment(1, "Hi.", "", "pending")
        self.clock.t += subtitles.PENDING_HOLD_S + subtitles.CLEAR_AFTER_S + 0.1
        self.assertIsNone(self.c.view())

    def test_stale_live_words_are_dropped_and_the_finished_sentence_stays(self):
        self.c.on_segment(1, "One.", "一", "final")
        self.c.on_preview("never finished")
        self.clock.t += subtitles.PREVIEW_STALE_S + 0.1
        v = self.c.view()
        self.assertEqual(v["current"][0], "One.")
        self.assertIsNone(v["previous"])

    def test_long_text_is_held_longer_but_capped(self):
        self.assertEqual(subtitles.SubtitleController.hold_s("a"), subtitles.MIN_HOLD_S)
        self.assertEqual(subtitles.SubtitleController.hold_s("a" * 500), subtitles.MAX_HOLD_S)

    def test_failed_translation_shows_a_marker_not_a_blank(self):
        self.c.on_segment(1, "Hi.", "", "failed")
        self.assertEqual(self.c.view()["current"], ("Hi.", "⚠"))

    def test_reset_clears_everything(self):
        self.c.on_segment(1, "Hi.", "x", "final")
        self.c.on_preview("live")
        self.c.reset()
        self.assertIsNone(self.c.view())


class Layouts(unittest.TestCase):
    def test_only_three_layouts_are_offered_to_the_viewer(self):
        self.assertEqual(subtitles.CHOICES, ("three", "four", "scroll"))
        self.assertEqual(subtitles.normalize_choice("two"), subtitles.DEFAULT_LAYOUT)     # no longer offered
        self.assertEqual(subtitles.normalize_choice("none"), subtitles.DEFAULT_LAYOUT)
        self.assertEqual(subtitles.normalize_choice("scroll"), "scroll")

    def test_scroll_lines_setting(self):
        self.assertEqual(subtitles.SCROLL_LINES_CHOICES, (2, 3, 4))
        self.assertEqual([subtitles.normalize_scroll_lines(v) for v in (2, "3", 4, 9, None, "x")],
                         [2, 3, 4, subtitles.DEFAULT_SCROLL_LINES, subtitles.DEFAULT_SCROLL_LINES,
                          subtitles.DEFAULT_SCROLL_LINES])

    def test_the_renderer_still_knows_every_layout(self):
        self.assertEqual(subtitles.LAYOUTS, ("none", "two", "three", "four", "scroll"))
        for name in subtitles.LAYOUTS:
            self.assertEqual(subtitles.normalize_layout(name), name)
        for junk in (None, "", "five", 3):
            self.assertEqual(subtitles.normalize_layout(junk), subtitles.DEFAULT_LAYOUT)


class Wrapping(unittest.TestCase):
    # a fake font: every character is 10 px wide, a CJK character too
    measure = staticmethod(lambda t: 10 * len(t))

    def test_words_are_kept_whole(self):
        self.assertEqual(subtitles.wrap_lines("one two three four", self.measure, 100),
                         ["one two", "three four"])

    def test_cjk_breaks_between_characters_and_punctuation_never_starts_a_line(self):
        lines = subtitles.wrap_lines("一二三四五六七八九，十", self.measure, 90)
        self.assertEqual(lines, ["一二三四五六七八九，", "十"])         # the comma stays with its line

    def test_a_word_longer_than_the_line_still_gets_its_own_line(self):
        self.assertEqual(subtitles.wrap_lines("short extraordinarily", self.measure, 60),
                         ["short", "extraordinarily"])

    def test_empty_text_has_no_lines(self):
        self.assertEqual(subtitles.wrap_lines("", self.measure, 90), [])
        self.assertEqual(subtitles.wrap_lines("   ", self.measure, 90), [])

    def test_the_ticker_keeps_the_most_recent_words(self):
        self.assertEqual(subtitles.tail_fit("alpha beta gamma delta", self.measure, 110), "gamma delta")
        self.assertEqual(subtitles.tail_fit("alpha beta", self.measure, 500), "alpha beta")
        self.assertEqual(subtitles.tail_fit("", self.measure, 100), "")
        self.assertEqual(subtitles.tail_fit("一二三四五六七八九十", self.measure, 50), "六七八九十")


class Settings(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(subtitles.clamp(5, 0.5, 2.0, 1.0), 2.0)
        self.assertEqual(subtitles.clamp(0.1, 0.5, 2.0, 1.0), 0.5)
        self.assertEqual(subtitles.clamp("x", 0.5, 2.0, 1.0), 1.0)
        self.assertEqual(subtitles.clamp(None, 0.5, 2.0, 1.0), 1.0)


class Monitors(unittest.TestCase):
    def test_at_least_one_monitor_with_a_primary(self):
        mons = subtitles.list_monitors()
        self.assertTrue(mons)
        self.assertTrue(any(m[4] for m in mons))

    def test_label(self):
        self.assertEqual(subtitles.monitor_label(0, (0, 0, 1920, 1080, True), "main"),
                         "1: 1920×1080 (main)")


if __name__ == "__main__":
    unittest.main()
