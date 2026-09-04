"""Finds other places in a recording that sound like a chosen snippet —
e.g. select one instance of a word or filler phrase ("um", "ah, I see")
that keeps recurring, and find every other place it was said too, so it
can be reviewed and removed throughout the whole recording in one pass.

The intent is "the same word spoken again", not "the exact same audio
byte-for-byte" — two utterances of the same word never have identical
waveforms (different pitch, slightly different pacing/phase), so
matching is done on each span's smoothed loudness *envelope* rather than
its raw waveform. Raw-waveform correlation requires near sample-perfect
phase alignment with the speaker's pitch period, which is why an early
version of this only ever found something close to a literal duplicate
of the query — the envelope is pitch- and phase-invariant and captures
just the word's loudness contour, which is what's actually similar
between two independent sayings of the same word.

Uses normalized cross-correlation (a matched filter) on a heavily
downsampled copy of the audio — full-sample-rate correlation over a
multi-minute recording would be far too slow for what's meant to feel
like an interactive search.

The query is sliced out of the *already-downsampled* buffer rather than
resampled separately from the full-rate audio: resampling a short
isolated snippet hits the polyphase filter's edge differently than
resampling it in place within the full buffer, and that mismatch alone
was enough to make the query's own original location come back as a 68%
match instead of the ~100% a perfect self-match should score (found via
a real search where the highlighted region itself showed up as its only
"similar" result, at 68%).

This still can't handle a repeat said noticeably slower or faster than
the query — the search window is a fixed length, so a real duration
mismatch (not just pitch/phase) falls outside what this can find without
proper time-warping, which isn't implemented here.
"""

from math import gcd

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import correlate, resample_poly

SEARCH_SAMPLE_RATE = 4000
# 0.8 (not the more permissive-sounding 0.5) because the normalization
# fix that made a genuine self-match score a mathematically exact 100%
# (previously ~68-95%, hidden behind a since-fixed bug that silently
# suppressed unrelated noise along with it) also means unrelated noise
# and silence can now legitimately clear a low bar like 0.5 — 0.8 is
# where real repeats of the query separate cleanly from that (verified
# against a synthetic "same word, different pitch" plus unrelated noise
# test: the real repeat scored ~99.8%, noise topped out well under 80%).
DEFAULT_THRESHOLD = 0.8
# Loudness-envelope smoothing window: long enough to average away a pitch
# period (speech F0 is rarely below ~70Hz, i.e. ~14ms) so two different
# utterances of the same word — different pitch, slightly different
# phase — collapse to essentially the same envelope shape, but still
# short enough to track syllable-scale loudness changes.
ENVELOPE_SMOOTH_MS = 15.0


def _resample(buffer, orig_sr, target_sr):
    if orig_sr == target_sr or buffer.size == 0:
        return buffer.astype(np.float32)
    g = gcd(int(orig_sr), int(target_sr))
    up, down = int(target_sr) // g, int(orig_sr) // g
    return resample_poly(buffer, up, down).astype(np.float32)


def _envelope(buffer, sample_rate, smooth_ms=ENVELOPE_SMOOTH_MS):
    smooth_samples = max(1, int(round(smooth_ms / 1000.0 * sample_rate)))
    return uniform_filter1d(np.abs(buffer), size=smooth_samples, mode="nearest")


def find_similar_segments(buffer, sample_rate, query_start_s, query_end_s,
                          threshold=DEFAULT_THRESHOLD,
                          search_sample_rate=SEARCH_SAMPLE_RATE,
                          progress_cb=None, cancel_cb=None):
    """Returns [(start_s, end_s, score), ...] sorted by position — every
    span in `buffer` that resembles [query_start_s, query_end_s), scored
    0..1 by normalized cross-correlation (1.0 = an exact shape match).
    This naturally includes the query's own original location too, since
    the whole buffer is searched — the caller distinguishes it by
    position, not by exclusion here.

    progress_cb(fraction, matches_so_far), if given, is called after each
    chunk of the search completes, so a caller can show a live "N found
    so far" status on a long recording instead of blocking silently.
    cancel_cb, if given, is polled between chunks — returning True stops
    the search early and returns whatever was found up to that point.
    """
    i0 = max(0, min(len(buffer), int(round(query_start_s * sample_rate))))
    i1 = max(0, min(len(buffer), int(round(query_end_s * sample_rate))))
    if i1 < i0:
        i0, i1 = i1, i0
    if i1 - i0 < 10:
        return []

    small_buffer = _resample(buffer, sample_rate, search_sample_rate)
    envelope = _envelope(small_buffer, search_sample_rate)
    qi0 = max(0, min(len(envelope), int(round(query_start_s * search_sample_rate))))
    qi1 = max(0, min(len(envelope), int(round(query_end_s * search_sample_rate))))
    if qi1 < qi0:
        qi0, qi1 = qi1, qi0
    small_query = envelope[qi0:qi1]
    window = len(small_query)
    if window < 4 or envelope.size < window:
        return []

    q = (small_query - small_query.mean()).astype(np.float64)
    q_norm = float(np.sqrt(np.sum(q * q))) + 1e-9
    # Deliberately NOT globally mean-centered: subtracting one fixed
    # buffer-wide mean from every window, then treating that as if it
    # were each window's own local mean, silently breaks normalization
    # whenever a window's true local mean differs from the whole
    # buffer's — which for an always-positive envelope signal is the
    # normal case (a loud region's envelope mean is nowhere near a quiet
    # region's), and even made the query's own original location fail to
    # score a perfect match. Since q above is already zero-mean,
    # correlate(raw, q) already equals sum((window - window_mean) * q)
    # with no centering needed on this side at all — the window_mean
    # term drops out algebraically because it's multiplied by q's own
    # sum, which is zero.
    raw = envelope.astype(np.float64)

    # Local mean/energy under each candidate window via two cumulative
    # sums (O(N)) rather than np.convolve(..., mode="valid") (O(N *
    # window)) — the O(N*window) version was the actual "find similar is
    # slow" bug: a several-second query window over a multi-minute
    # recording made the convolve call alone take many seconds, dwarfing
    # the fast FFT correlation next to it.
    s1 = np.cumsum(np.insert(raw, 0, 0.0))
    s2 = np.cumsum(np.insert(raw ** 2, 0, 0.0))

    total = raw.size
    min_gap = max(1, window)
    # Processed in chunks — not for speed (a single whole-buffer pass is
    # already fast once the convolve above is gone) but so a caller can
    # report progress and cancel mid-search on a very long recording
    # instead of blocking for however long the whole thing takes. Chunks
    # are placed back-to-back with no overlap in the candidate positions
    # each one reports, so a match is still considered exactly once no
    # matter which chunk its start position falls in.
    chunk_samples = max(window * 4, search_sample_rate * 20)
    matches = []  # [start_small, end_small, score]
    pos = 0
    while pos < total:
        if cancel_cb is not None and cancel_cb():
            break
        ctx_start = max(0, pos - window + 1) if pos > 0 else 0
        chunk_end = min(total, pos + chunk_samples)
        chunk = raw[ctx_start:chunk_end]
        if chunk.size >= window:
            raw_scores = correlate(chunk, q, mode="valid", method="fft")
            n_local = raw_scores.shape[0]
            sum1 = (s1[ctx_start + window:ctx_start + window + n_local]
                   - s1[ctx_start:ctx_start + n_local])
            sum2 = (s2[ctx_start + window:ctx_start + window + n_local]
                   - s2[ctx_start:ctx_start + n_local])
            local_energy = np.maximum(sum2 - (sum1 ** 2) / window, 1e-9)
            local_norm = np.sqrt(local_energy)
            scores = raw_scores / (local_norm * q_norm)
            candidate_idx = np.where(scores >= threshold)[0]
            for local_idx in candidate_idx:
                idx = ctx_start + local_idx
                score = float(scores[local_idx])
                # A real match's score stays high across a neighborhood
                # roughly one query-length wide (the correlation doesn't
                # drop sharply the instant the window is off by a few
                # samples) — using half that as the dedup distance left
                # two detections ~window/2 apart for the same occurrence
                # (found via a real repeated-tone test); a full window's
                # width is what actually collapses them to one.
                if matches and idx - matches[-1][0] < min_gap:
                    if score > matches[-1][2]:
                        matches[-1] = [idx, idx + window, score]
                else:
                    matches.append([idx, idx + window, score])
        pos = chunk_end
        if progress_cb is not None:
            query_duration = query_end_s - query_start_s
            results_so_far = [
                (s / search_sample_rate, s / search_sample_rate + query_duration, sc)
                for s, _e, sc in matches
            ]
            progress_cb(min(1.0, pos / total), results_so_far)

    query_duration = query_end_s - query_start_s
    return [(s / search_sample_rate, s / search_sample_rate + query_duration, sc)
           for s, _e, sc in matches]
