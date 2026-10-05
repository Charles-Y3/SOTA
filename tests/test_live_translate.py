"""Pure-logic tests for live_translate (no models, no mic).
Run:  .venv\Scripts\python.exe -m unittest tests.test_live_translate -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_translate as ltr


class SplitSentences(unittest.TestCase):
    def test_english(self):
        self.assertEqual(ltr.split_sentences("Hello there. How are you? Fine"),
                         (["Hello there.", "How are you?"], "Fine"))

    def test_chinese_no_space(self):
        self.assertEqual(ltr.split_sentences("今天天气很好。我们去公园！然后"),
                         (["今天天气很好。", "我们去公园！"], "然后"))

    def test_decimal_and_abbreviation_not_boundaries(self):
        self.assertEqual(ltr.split_sentences("It costs 3.5 dollars, said Dr. Smith. Ok"),
                         (["It costs 3.5 dollars, said Dr. Smith."], "Ok"))

    def test_trailing_terminator_is_complete(self):
        self.assertEqual(ltr.split_sentences("Done."), (["Done."], ""))

    def test_empty(self):
        self.assertEqual(ltr.split_sentences(""), ([], ""))

    def test_quote_closer_kept(self):
        self.assertEqual(ltr.split_sentences("他说：「好。」然后走了"),
                         (["他说：「好。」"], "然后走了"))


class Stream(unittest.TestCase):
    def test_sentence_released_after_stable_ticks(self):
        s = ltr.SentenceStream(stable_ticks=2)
        self.assertEqual(s.update("Hello there. How a"), ([], "Hello there. How a", None))
        self.assertEqual(s.update("Hello there. How are you"), (["Hello there."], "How are you", None))

    def test_last_sentence_waits_for_commit(self):
        # The decoder full-stops whatever it has heard so far; a lone
        # "complete" sentence at the end may still be cut off mid-speech.
        s = ltr.SentenceStream(stable_ticks=1)
        self.assertEqual(s.update("This is yeah."), ([], "This is yeah.", None))
        self.assertEqual(s.update("This is yeah. And then we"), (["This is yeah."], "And then we", None))

    def test_latency_profile_releases_immediately(self):
        s = ltr.SentenceStream(stable_ticks=1)
        self.assertEqual(s.update("Hello there. How a"), (["Hello there."], "How a", None))

    def test_revised_sentence_waits_again(self):
        s = ltr.SentenceStream(stable_ticks=2)
        s.update("Hello their. More")
        out, _, _ = s.update("Hello there. More")      # decoder changed its mind
        self.assertEqual(out, [])
        out, _, _ = s.update("Hello there. More words")
        self.assertEqual(out, ["Hello there."])

    def test_never_released_twice(self):
        s = ltr.SentenceStream(stable_ticks=1)
        self.assertEqual(s.update("A b c. D e")[0], ["A b c."])
        self.assertEqual(s.update("A b c. D e f. G")[0], ["D e f."])
        out, _, _ = s.update("A b c. D e f. G h", final=True)
        self.assertEqual(out, ["G h"])

    def test_final_flushes_fragment_and_resets(self):
        s = ltr.SentenceStream(stable_ticks=2)
        out, prev, cut = s.update("Only a fragment", final=True)
        self.assertEqual((out, prev, cut), (["Only a fragment"], "", None))
        self.assertEqual(s.emitted, 0)

    def test_preview_excludes_released(self):
        s = ltr.SentenceStream(stable_ticks=1)
        _, prev, _ = s.update("你好。我是小明")
        self.assertEqual(prev, "我是小明")


class Timestamps(unittest.TestCase):
    TOKENS = [("Good", 0.15), ("morning", 0.39), (",", 0.63), ("everyone", 0.75), (".", 1.11),
              ("Thank", 2.01), ("you", 2.31), (".", 2.7), ("Our", 4.59), ("results", 5.0)]
    TEXT = "Good morning, everyone. Thank you. Our results"

    def test_punctuation_units_with_cuts_in_the_gaps(self):
        units, frag = ltr.segment(self.TEXT, self.TOKENS, gap_s=5.0)
        self.assertEqual([u[0] for u in units], ["Good morning, everyone.", "Thank you."])
        self.assertEqual(frag, "Our results")
        self.assertTrue(1.11 < units[0][1] < 2.01 - ltr.CUT_MARGIN_S + 1e-9)
        self.assertTrue(2.7 < units[1][1] < 4.59)
        self.assertFalse(units[0][2] or units[1][2])

    def test_pause_splits_where_decoder_used_a_comma(self):
        toks = [("Thanks", 0.1), ("everyone", 0.5), (",", 0.9), ("our", 2.0), ("results", 2.4)]
        units, frag = ltr.segment("Thanks everyone, our results", toks, gap_s=0.8)
        self.assertEqual([u[0] for u in units], ["Thanks everyone,"])
        self.assertTrue(units[0][2])                       # closed by a pause
        self.assertTrue(0.9 < units[0][1] < 2.0)
        self.assertEqual(frag, "our results")

    def test_no_pause_split_when_gap_is_short(self):
        toks = [("a", 0.1), ("b", 0.6), ("c", 1.1)]
        units, frag = ltr.segment("a b c", toks, gap_s=0.8)
        self.assertEqual((units, frag), ([], "a b c"))

    def test_last_sentence_has_no_cut(self):
        units, frag = ltr.segment("Hi.", [("Hi", 0.1), (".", 0.5)], gap_s=0.8)
        self.assertEqual(units, [("Hi.", None, False)])

    def test_abbreviation_not_a_boundary(self):
        units, frag = ltr.segment("Dr. Smith.", [("Dr", 0.1), (".", 0.3), ("Smith", 0.5), (".", 0.9)], 0.8)
        self.assertEqual([u[0] for u in units], ["Dr. Smith."])

    def test_cjk_tokens(self):
        toks = [("你", 0.1), ("好", 0.2), ("。", 0.4), ("我", 1.5), ("是", 1.6)]
        units, frag = ltr.segment("你好。我是", toks, gap_s=5.0)
        self.assertEqual([u[0] for u in units], ["你好。"])
        self.assertEqual(frag, "我是")
        self.assertTrue(0.4 < units[0][1] < 1.5)

    def test_mismatching_tokens_fall_back_to_text_split(self):
        units, frag = ltr.segment("One. Two. Three", [("One", 0.1), (".", 0.3)], gap_s=0.8)
        self.assertEqual([u[0] for u in units], ["One.", "Two."])
        self.assertTrue(all(u[1] is None for u in units))
        self.assertEqual(frag, "Three")

    def test_stream_returns_cut_and_restarts(self):
        s = ltr.SentenceStream(stable_ticks=1, gap_s=5.0)
        out, prev, cut = s.update(self.TEXT, self.TOKENS)
        self.assertEqual(out, ["Good morning, everyone.", "Thank you."])
        self.assertIsNotNone(cut)
        self.assertGreater(cut, 2.7)
        self.assertEqual(prev, "Our results")
        self.assertEqual(s.emitted, 0)             # state restarts with the new tail

    def test_pause_closed_unit_needs_one_sighting_even_with_stable_ticks_2(self):
        s = ltr.SentenceStream(stable_ticks=2, gap_s=0.8)
        toks = [("Thanks", 0.1), ("everyone", 0.5), (",", 0.9), ("our", 2.0), ("results", 2.4)]
        out, _, cut = s.update("Thanks everyone, our results", toks)
        self.assertEqual(out, ["Thanks everyone,"])
        self.assertIsNotNone(cut)

    def test_misaligned_tokens_fall_back_to_index_skipping(self):
        s = ltr.SentenceStream(stable_ticks=1)
        out, _, cut = s.update("One. Two. Three", [("One", 0.1), (".", 0.3)])
        self.assertEqual(out, ["One.", "Two."])
        self.assertIsNone(cut)
        self.assertEqual(s.emitted, 2)


class Misc(unittest.TestCase):
    def test_meaningful(self):
        for ok in ("谢谢。", "Hi.", "ok"):
            self.assertTrue(ltr.is_meaningful(ok), ok)
        for bad in ("", ".", "?!", "a"):
            self.assertFalse(ltr.is_meaningful(bad), bad)

    def test_profiles_ordered_by_latency(self):
        p = ltr.PROFILES
        self.assertEqual(sorted(p), sorted(ltr.PROFILE_KEYS))
        self.assertEqual(list(p), ["latency", "accuracy"])          # just two modes
        self.assertLess(p["latency"]["pause_s"], p["accuracy"]["pause_s"])
        self.assertLessEqual(p["latency"]["context_pairs"], p["accuracy"]["context_pairs"])
        self.assertTrue(p["latency"]["chunks"])                     # translates clauses while you speak
        self.assertFalse(p["accuracy"]["chunks"])

    def test_the_old_balanced_mode_is_now_called_accuracy(self):
        self.assertEqual(ltr.PROFILE_ALIASES["balanced"], "accuracy")
        w = ltr.LiveTranslator(None, profile="balanced")
        self.assertEqual(w.profile_key, "accuracy")
        self.assertEqual(ltr.LiveTranslator(None).profile_key, "latency")           # the default
        self.assertEqual(ltr.LiveTranslator(None, profile="nonsense").profile_key, "latency")
        import i18n
        self.assertEqual([i18n.normalize_lt_mode(v) for v in ("latency", "accuracy", "balanced", None, "x")],
                         ["latency", "accuracy", "accuracy", "latency", "latency"])
        self.assertEqual(i18n.LT_MODE_KEYS, ["latency", "accuracy"])
        self.assertEqual(i18n.lt_mode_options("en"), ["Low latency", "Accuracy"])

    def test_calibration_validity(self):
        good = {"version": ltr.CALIBRATION_VERSION, "cores": os.cpu_count() or 4,
                "llama_threads": 2, "torch_threads": 2}
        self.assertTrue(ltr.calibration_is_valid(good))
        self.assertFalse(ltr.calibration_is_valid(dict(good, cores=999)))
        self.assertFalse(ltr.calibration_is_valid(None))

    def test_join_text(self):
        self.assertEqual(ltr.join_text("你好", "世界"), "你好世界")
        self.assertEqual(ltr.join_text("Hello", "world"), "Hello world")


class RobustnessHelpers(unittest.TestCase):
    def test_quiet_voice_counts_as_speech_but_room_noise_does_not(self):
        import numpy as np
        rng = np.random.default_rng(0)
        noise = rng.normal(0, 0.003, 16000).astype("float32")                      # quiet room
        voice = (np.sin(np.arange(16000) * 0.05) * 0.015).astype("float32") + noise   # RMS ~0.011
        thr = ltr.speech_threshold(float(np.percentile(ltr.frame_rms(noise), 10)))
        self.assertFalse(ltr.has_speech(ltr.frame_rms(noise), thr))
        self.assertTrue(ltr.has_speech(ltr.frame_rms(voice), thr))

    def test_gain_lifts_quiet_speech_only(self):
        import numpy as np
        quiet = np.full(16000, 0.01, dtype="float32")
        loud = np.full(16000, 0.2, dtype="float32")
        self.assertGreater(ltr.speech_gain(ltr.frame_rms(quiet), 0.003), 3.0)
        self.assertEqual(ltr.speech_gain(ltr.frame_rms(loud), 0.003), 1.0)
        self.assertLessEqual(ltr.speech_gain(ltr.frame_rms(quiet * 0.05), 0.0001), ltr.MAX_GAIN)

    def test_trailing_silence(self):
        import numpy as np
        a = np.concatenate([np.full(16000, 0.1, "float32"), np.zeros(16000, "float32")])
        f = ltr.frame_rms(a)
        self.assertTrue(ltr.trailing_silent(f, 0.8, 0.01))
        self.assertFalse(ltr.trailing_silent(f, 1.5, 0.01))

    def test_language_vote_ignores_one_bad_detection(self):
        v = ltr.LanguageVote("")
        v.add("zh", 3.0, "hello there my friend")          # one stray detection
        self.assertEqual(v.current, "")
        for _ in range(3):
            v.add("en", 3.0, "how are you today")
        self.assertEqual(v.current, "en")
        v.add("zh", 3.0, "x y z")
        v.add("zh", 3.0, "x y z")
        self.assertEqual(v.current, "en")                  # two of five do not replace it
        v.add("zh", 3.0, "x y z")
        self.assertEqual(v.current, "zh")                  # three of the last five do

    def test_language_vote_short_tails_and_user_choice(self):
        v = ltr.LanguageVote("")
        for _ in range(5):
            v.add("ja", 1.0, "hello")                      # tails under 2 s are not trusted
        self.assertEqual(v.current, "")
        fixed = ltr.LanguageVote("ko")
        for _ in range(5):
            fixed.add("en", 5.0, "hello there")
        self.assertEqual(fixed.current, "ko")

    def test_same_language_shortcut_needs_the_text_to_agree(self):
        self.assertTrue(ltr.looks_like_target("你好，今天天气不错。", "zh-hant"))
        self.assertFalse(ltr.looks_like_target("こんにちは、元気ですか。", "zh-hant"))   # kana
        self.assertFalse(ltr.looks_like_target("안녕하세요", "zh-hans"))
        self.assertTrue(ltr.looks_like_target("Good morning everyone.", "en"))
        self.assertFalse(ltr.looks_like_target("你好", "en"))
        self.assertFalse(ltr.looks_like_target("Bonjour", "fr"))                        # never skipped

    def test_translation_gate(self):
        ok = ltr.translation_ok
        self.assertTrue(ok("Good morning everyone.", "大家早上好。", "zh-hant"))
        self.assertFalse(ok("Good morning everyone.", "", "zh-hant"))
        self.assertFalse(ok("Good morning everyone.", "Good morning everyone.", "zh-hant"))   # echo
        self.assertFalse(ok("Good morning everyone", "好" * 400, "zh-hant"))                  # absurd length
        self.assertFalse(ok("This is a sentence.", "这是一个句子。", "en"))                   # wrong script
        self.assertFalse(ok("This is a fine sentence", "abc abc abc abc abc abc abc abc", "fr"))  # loop

    def test_cap_split_picks_the_biggest_late_gap(self):
        toks = [("a", 0.0), ("b", 0.4), ("c", 0.8), ("d", 1.2), ("e", 3.0), ("f", 3.4), ("g", 3.8)]
        left, cut, n = ltr.cap_split(toks)
        self.assertEqual(left, "a b c d")
        self.assertTrue(1.2 < cut < 3.0)
        self.assertEqual(n, 4)

    def test_cap_split_gives_up_without_a_real_gap(self):
        toks = [(c, i * 0.3) for i, c in enumerate("abcdefgh")]
        self.assertIsNone(ltr.cap_split(toks))

    def test_confirm_after_releases_sentence_with_newer_speech_behind_it(self):
        toks = [("Hello", 0.1), ("there", 0.4), (".", 0.8), ("More", 1.0), ("words", 1.3), ("now", 3.0)]
        s = ltr.SentenceStream(stable_ticks=2, gap_s=5.0, confirm_after_s=1.5)
        out, _prev, _cut = s.update("Hello there. More words now", toks, tail_s=3.4)
        self.assertEqual(out, ["Hello there."])           # 2.6 s of speech follows it
        s2 = ltr.SentenceStream(stable_ticks=2, gap_s=5.0, confirm_after_s=None)
        self.assertEqual(s2.update("Hello there. More words now", toks, tail_s=3.4)[0], [])


class ChunkedTranslation(unittest.TestCase):
    def test_clauses_are_split_at_commas_and_the_open_part_is_kept(self):
        chunks, rest = ltr.plan_chunks("First, we need to finish the design, and then we move on")
        self.assertEqual(chunks, ["First, we need to finish the design,"])   # "First," alone is too short
        self.assertEqual(rest, "and then we move on")

    def test_short_clauses_merge_with_the_next(self):
        chunks, rest = ltr.plan_chunks("Well, yes, I think so, but we should check the numbers first")
        self.assertEqual(chunks, ["Well, yes, I think so,"])
        self.assertTrue(rest.startswith("but we should"))

    def test_a_sentence_without_commas_is_not_chunked(self):
        self.assertEqual(ltr.plan_chunks("Thank you for joining the call today"),
                         ([], "Thank you for joining the call today"))

    def test_a_very_long_unpunctuated_run_is_split(self):
        text = " ".join(f"w{i}" for i in range(20))
        chunks, rest = ltr.plan_chunks(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].split(), [f"w{i}" for i in range(ltr.CHUNK_FORCE_AT)])
        self.assertEqual(len(rest.split()), 20 - ltr.CHUNK_FORCE_AT)

    def test_cjk_clauses(self):
        chunks, rest = ltr.plan_chunks("首先我們需要完成這個設計工作，然後再進行測試和驗收")
        self.assertEqual(chunks, ["首先我們需要完成這個設計工作，"])
        self.assertEqual(rest, "然後再進行測試和驗收")

    def test_a_clause_is_handed_out_only_once_it_reads_the_same_twice(self):
        t = ltr.ChunkTracker()
        text = "First, we need to finish the design, and then we"
        self.assertEqual(t.update(text), ([], False))                    # first sighting
        new, stale = t.update(text + " move on")
        self.assertEqual(new, [(0, "First, we need to finish the design,")])
        self.assertFalse(stale)
        self.assertEqual(t.update(text + " move on to testing")[0], [])  # never twice

    def test_a_revised_clause_marks_the_early_work_stale(self):
        t = ltr.ChunkTracker()
        t.update("First, we need to finish the design, and then")
        t.update("First, we need to finish the design, and then we")
        new, stale = t.update("First we need to finish a different design, and then we move")
        self.assertTrue(stale)

    def test_prefix_split_returns_the_rest_of_the_sentence(self):
        src = "First, we need to finish the design, and then we move on to testing."
        self.assertEqual(ltr.prefix_split(src, ["First, we need to finish the design,"]),
                         "and then we move on to testing.")

    def test_prefix_split_tolerates_small_recognizer_changes_only(self):
        src = "First we need to finish the design, and then we test."
        self.assertEqual(ltr.prefix_split(src, ["First, we need to finish the design,"]),
                         "and then we test.")
        self.assertIsNone(ltr.prefix_split("Something else entirely, and more words here.",
                                           ["First, we need to finish the design,"]))

    def test_prefix_split_when_the_sentence_ends_with_the_clause(self):
        self.assertEqual(ltr.prefix_split("I think so, yes.", ["I think so, yes."]), "")

    def test_join_uses_the_target_languages_comma(self):
        self.assertEqual(ltr.join_translations("首先，我們需要完成設計。", "然後再測試。", "zh-hant"),
                         "首先，我們需要完成設計，然後再測試。")
        self.assertEqual(ltr.join_translations("First we design.", "then we test.", "en"),
                         "First we design, then we test.")
        self.assertEqual(ltr.join_translations("", "x", "en"), "x")
        self.assertEqual(ltr.join_translations("x，", "y", "zh-hant"), "x，y")


if __name__ == "__main__":
    unittest.main()
