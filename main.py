"""
AI Band Transcriber - Main Pipeline CLI
Transforms a song into Drums, Bass, Lead Guitar, and Rhythm Guitar audio tracks, MIDIs, and MusicXML Tablature.
"""

import os
import sys
import argparse

# Add src to python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.pipeline import run_pipeline, VRAM_PROFILES


def main():
    parser = argparse.ArgumentParser(description="AI Band Transcriber (Drums, Bass, Lead, Rhythm -> TAB & Score)")
    parser.add_argument("-i", "--input", required=True, help="Path to input audio file or YouTube URL")
    parser.add_argument("-o", "--output", default="output", help="Output root folder; each song goes to <output>/<song>/ (default: ./output)")
    parser.add_argument("--skip-separation", action="store_true", help="Reuse this song's existing stems instead of re-running Demucs")
    parser.add_argument("--parts", default="drums,bass,rhythm,lead",
                        help="Comma-separated parts to include: drums, bass, rhythm, lead, vocals "
                             "(vocals = vocal melody with lyrics, e.g. 'vocals,lead')")
    parser.add_argument("--style", default="tab", choices=["tab", "standard", "both"], help="PDF style: tab, standard, both")
    parser.add_argument("--vram", choices=sorted(VRAM_PROFILES), default=None,
                        help="GPU memory profile: 8gb or 4gb (default: detected from the GPU)")
    parser.add_argument("--lyrics-lang", default=None,
                        help="Lyrics language code for Whisper, e.g. ko, en, ja (default: detected)")
    parser.add_argument("--no-lyrics", action="store_true", help="Transcribe the vocal melody without lyrics")
    args = parser.parse_args()

    selected_parts = [p.strip() for p in args.parts.split(",") if p.strip()]
    try:
        run_pipeline(args.input, args.output, parts=selected_parts, style=args.style,
                     skip_separation=args.skip_separation, vram=args.vram,
                     lyrics=not args.no_lyrics, lyrics_language=args.lyrics_lang)
    except FileNotFoundError as e:
        print(f"[!] Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
