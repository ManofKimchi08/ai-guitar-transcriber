"""
Pipeline Options Module
The choices a pipeline run takes (parts, VRAM profiles) and the stages it announces,
kept apart from src.pipeline so the GUI and the local engine server can validate
options without importing PyTorch and the models.
"""

ALL_PARTS = ['drums', 'bass', 'rhythm', 'lead', 'vocals']
DEFAULT_PARTS = ['drums', 'bass', 'rhythm', 'lead']

# GPU memory profiles. Peak activations measured per setting: htdemucs ~0.7 GB at
# its default 7.8 s segment (~0.5 GB at 5 s); TorchCREPE 'full' ~2.5 GB per 1024
# frames. Both steps also retry with smaller settings if the GPU still runs out.
# Whisper (lyrics): large-v3 in float16 needs ~4.5 GB; large-v3-turbo in int8 far less.
VRAM_PROFILES = {
    "8gb": {"label": "8GB 이상 (표준)", "demucs_segment": None, "crepe_batch_size": 1024,
            "whisper_model": "large-v3", "whisper_compute": "float16"},
    "4gb": {"label": "4GB (저사양)", "demucs_segment": 5, "crepe_batch_size": 512,
            "whisper_model": "large-v3-turbo", "whisper_compute": "int8_float16"},
}


def vram_profile_for(total_gb: float) -> str:
    """Maps a GPU's total memory to a VRAM profile ("8 GB" cards report slightly under 8 GiB)."""
    return "8gb" if total_gb >= 7.0 else "4gb"


def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


# Stage keys announced through run_pipeline's `stage` callback, in order
STAGES = ("download", "separate", "split", "transcribe", "lyrics", "score")
