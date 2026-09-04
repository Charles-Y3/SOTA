"""Noise reduction for Audio Studio's Enhance subtab.

DeepFilterNet (MIT license) is the primary engine: much better on
non-stationary noise (fan/traffic/keyboard, not just steady hiss) than
spectral gating, and its ~2MB pretrained checkpoint is fetched and cached
by its own init_df() the first time it runs — no separate download-
progress plumbing is needed the way Whisper/SenseVoice's model manager
needs one, since the file is tiny and DeepFilterNet handles its own
caching. noisereduce (spectral gating, MIT, zero downloads at all) is the
fallback for anyone who declines/can't reach that download, or if
DeepFilterNet fails to import/initialize for any reason — Enhance's
"Apply" action should never hard-fail over an optional dependency this
deep in the stack.
"""

from math import gcd

import numpy as np

_df_cache = {}


def deepfilternet_available():
    try:
        import df.enhance  # noqa: F401
    except Exception:
        return False
    return True


def _get_df():
    if "model" not in _df_cache:
        from df.enhance import init_df

        model, df_state, _ = init_df()
        _df_cache["model"] = model
        _df_cache["state"] = df_state
    return _df_cache["model"], _df_cache["state"]


def _resample(buffer, orig_sr, target_sr):
    if orig_sr == target_sr or buffer.size == 0:
        return buffer.astype(np.float32)
    from scipy.signal import resample_poly

    g = gcd(orig_sr, target_sr)
    up, down = target_sr // g, orig_sr // g
    return resample_poly(buffer, up, down).astype(np.float32)


def denoise_deepfilternet(buffer, sample_rate):
    """Runs DeepFilterNet on a mono float32 buffer at any sample rate —
    DeepFilterNet trains at a fixed rate of its own (48kHz), unrelated to
    Audio Studio's editing rate, so this resamples there and back."""
    import torch
    from df.enhance import enhance

    model, df_state = _get_df()
    model_sr = df_state.sr()
    audio = _resample(buffer, sample_rate, model_sr)
    tensor = torch.from_numpy(audio).unsqueeze(0)  # (channels=1, samples)
    enhanced = enhance(model, df_state, tensor)
    enhanced = enhanced.squeeze(0).detach().cpu().numpy().astype(np.float32)
    return _resample(enhanced, model_sr, sample_rate)


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
    tends to beat blind/model-based denoising on noise that's unusual or
    specific to one recording (a particular fridge hum, a fan), since
    it's tuned to that exact noise rather than general speech patterns."""
    return denoise_spectral_gate(buffer, sample_rate, noise_clip=noise_profile)


def denoise(buffer, sample_rate, prefer_deepfilternet=True, prop_decrease=0.8, stationary=False):
    """Best-effort dispatcher — tries DeepFilterNet first (if preferred),
    silently falls back to noisereduce on any failure (package missing,
    first-run download failing offline, unexpected runtime error).
    Returns (denoised_buffer, engine_used) so the caller can tell the user
    which one actually ran, since the two differ noticeably in quality.

    prop_decrease/stationary only affect the noisereduce fallback (there's
    no equivalent knob on DeepFilterNet) — see denoise_spectral_gate for
    what they do and why a blind/automatic caller should hand in gentler
    values than the manual Enhance row's own defaults."""
    if prefer_deepfilternet:
        try:
            return denoise_deepfilternet(buffer, sample_rate), "deepfilternet"
        except Exception:
            pass
    return denoise_spectral_gate(buffer, sample_rate, prop_decrease=prop_decrease,
                                 stationary=stationary), "noisereduce"
