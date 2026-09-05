# -*- mode: python ; coding: utf-8 -*-
# macOS build spec. Exists (rather than reusing plain CLI flags like the
# Windows job) because the microphone-permission key must be baked into
# Info.plist AT BUILD TIME: editing the plist after PyInstaller has
# assembled the .app invalidates the ad-hoc code signature it applied
# while assembling, and Apple-silicon Macs refuse to launch an app whose
# signature doesn't verify — the app dies instantly on double-click with
# no error dialog (this shipped as v1.2.1's macOS build). BUNDLE's
# info_plist writes the key before PyInstaller signs, so the bundle is
# signed exactly once, over its final contents.
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import collect_data_files

datas = []
binaries = []
hiddenimports = ['onnxruntime']
datas += collect_data_files('faster_whisper')
# scipy/noisereduce/pyloudnorm/PIL/lameenc: Audio Studio (v2)'s own
# dependencies, not in this list until now — see the matching comment in
# .github/workflows/build.yml's Windows job for why that's a real gap
# (the --selftest smoke test never exercises Enhance/Denoise/Export, so a
# packaging miss here would only surface when an actual user clicked one
# of those buttons in the built .app). DeepFilterNet ('df') was removed —
# it never actually ran (a torchaudio API it needs was removed in the
# torchaudio version pinned in requirements.txt) — the AI panel's NSNet2
# model runs through onnxruntime instead (already hidden-imported above,
# needed by faster-whisper's own VAD regardless).
for pkg in ('customtkinter', 'tkinterdnd2', 'sounddevice', 'av', 'docx',
            'ctranslate2', 'llama_cpp', 'opencc', 'funasr', 'torch',
            'torchaudio', 'scipy', 'noisereduce', 'pyloudnorm',
            'PIL', 'lameenc'):
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SOTA',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='SOTA',
)
app = BUNDLE(
    coll,
    name='SOTA.app',
    icon=None,
    bundle_identifier='com.sota.transcription',
    info_plist={
        # Without this key macOS doesn't prompt for mic access — it kills
        # the process the moment Live Transcription opens the input stream.
        'NSMicrophoneUsageDescription':
            'SOTA uses the microphone to record and transcribe your speech'
            ' in the Live Transcription tab.',
        'NSHighResolutionCapable': True,
    },
)
