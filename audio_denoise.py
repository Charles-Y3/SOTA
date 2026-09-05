"""Noise reduction for Audio Studio's Enhance subtab.

Deliberately the "traditional" (non-ML) noise-reduction method only —
noisereduce's spectral gating: it estimates a noise profile from the
buffer (or from a user-captured reference clip) and subtracts it in the
frequency domain, zero downloads, zero model weights. The ML-based option
(NSNet2, via audio_ai_edit.py) lives exclusively in the Edit tab's AI
panel — kept out of this module on purpose, so Enhance's manual row stays
a plain, fully-deterministic DSP tool a user can reason about directly,
and the AI panel is the only place a model-driven result appears. (This
module previously also tried DeepFilterNet first; it was removed because
it never actually ran — a torchaudio API it depends on was removed in the
torchaudio version pinned in requirements.txt, so it always silently fell
back to noisereduce anyway. See git history for the removed code.)
"""

import numpy as np


def denoise_spectral_gate(buffer, sample_rate, prop_decrease=0.8, stationary=False,
                          noise_clip=None):
    """noise_clip, if given, is a short reference recording of just the
    noise (no speech) — noisereduce profiles that specific noise (via its
    own y_noise parameter) instead of inferring one from the buffer
    itself, the classic Audacity "Get Noise Profile" workflow. Forces
    stationary=True when a profile is given: a fixed profile only makes
    sense against noise that isn't changing over time.

    prop_decrease/stationary trade effectiveness for safety: the default
    (0.8, non-stationary — i.e. re-estimating the noise per frame) is
    aggressive and, without a profile to ground it, prone to "musical
    noise" (warbling artifacts) and over-suppressing quiet speech it
    mistakes for noise — measured on a real quiet recording, the default
    cut overall RMS by nearly half. A lower prop_decrease and
    stationary=True (a single fixed noise estimate for the whole buffer,
    not re-guessed every frame) trade some noise-reduction strength for
    a result that's far less likely to mangle the actual voice — the
    right trade for an unsupervised/automatic caller that can't preview
    the result first."""
    import noisereduce as nr

    kwargs = {"y": buffer, "sr": sample_rate, "prop_decrease": prop_decrease}
    if noise_clip is not None:
        kwargs["y_noise"] = noise_clip
        kwargs["stationary"] = True
    else:
        kwargs["stationary"] = stationary
    return np.asarray(nr.reduce_noise(**kwargs), dtype=np.float32)


def denoise_with_profile(buffer, sample_rate, noise_profile):
    """Denoises `buffer` using `noise_profile` (a short noise-only
    recording captured elsewhere in the same clip) as the reference —
    tends to beat blind noise-profile guessing on noise that's unusual or
    specific to one recording (a particular fridge hum, a fan), since
    it's tuned to that exact noise rather than general speech patterns."""
    return denoise_spectral_gate(buffer, sample_rate, noise_clip=noise_profile)


def denoise(buffer, sample_rate, prop_decrease=0.8, stationary=False):
    """Enhance's Noise reduction row — noisereduce's spectral gating,
    always. Returns (denoised_buffer, engine_used) — "noisereduce" is the
    only possible value, but the shape is kept so callers (and the
    "Engine: {engine}" status label) don't need to special-case a single-
    engine dispatcher versus a multi-engine one."""
    return denoise_spectral_gate(buffer, sample_rate, prop_decrease=prop_decrease,
                                 stationary=stationary), "noisereduce"
