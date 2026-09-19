"""
AI Band Transcriber - Main Pipeline CLI
Transforms a song into Drums, Bass, Lead Guitar, and Rhythm Guitar audio tracks, MIDIs, and MusicXML Tablature.
"""

import os
import sys
import argparse
import time
from pathlib import Path

# Add src to python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.separator import separate_stems
from src.guitar_splitter import split_guitar_track
from src.drum_transcriber import transcribe_drums
from src.pitch_transcriber import transcribe_pitch
from src.tab_builder import build_musicxml_score
from src.youtube_downloader import download_youtube_audio
from src.pdf_exporter import export_score_to_pdf


def run_pipeline(input_audio: str, output_dir: str, skip_separation: bool = False, parts: list = None, style: str = "tab"):
    start_time = time.time()
    
    out_dir = os.path.abspath(output_dir)
    os.makedirs(out_dir, exist_ok=True)

    # Check if input is a YouTube URL
    if input_audio.startswith("http://") or input_audio.startswith("https://"):
        print(f"[*] YouTube URL detected: {input_audio}")
        print("[*] Downloading highest quality audio via yt-dlp & FFmpeg...")
        input_path, song_title = download_youtube_audio(input_audio, out_dir)
        print(f"[+] Downloaded: {song_title} -> {input_path}")
    else:
        input_path = os.path.abspath(input_audio)
        if not os.path.exists(input_path):
            print(f"[!] Error: Input file not found: {input_path}")
            sys.exit(1)
        song_title = Path(input_path).stem
    stems_dir = os.path.join(out_dir, "stems")
    midi_dir = os.path.join(out_dir, "midi")
    score_dir = os.path.join(out_dir, "scores")
    
    os.makedirs(stems_dir, exist_ok=True)
    os.makedirs(midi_dir, exist_ok=True)
    os.makedirs(score_dir, exist_ok=True)

    song_title = Path(input_path).stem
    print("=" * 65)
    print(f"       AI BAND TRANSCRIBER & TAB GENERATOR")
    print(f"       Target: {song_title}")
    print("=" * 65)

    # Normalize parts
    if not parts:
        selected_parts = ['drums', 'bass', 'rhythm', 'lead']
    else:
        selected_parts = [p.strip().lower() for p in parts if p.strip()]
    print(f"[*] Selected Output Parts: {', '.join(selected_parts)}")

    # -------------------------------------------------------------
    # Step 1: Stem Separation (Demucs)
    # -------------------------------------------------------------
    drums_wav = os.path.join(stems_dir, "drums.wav")
    bass_wav = os.path.join(stems_dir, "bass.wav")
    guitar_wav = os.path.join(stems_dir, "guitar.wav")
    
    if not skip_separation or not (os.path.exists(drums_wav) and os.path.exists(guitar_wav)):
        print("\n[Step 1/4] Separating instruments with Meta Demucs (htdemucs_6s)...")
        stems = separate_stems(input_path, out_dir, model_name="htdemucs_6s")
        drums_wav = stems.get("drums", drums_wav)
        bass_wav = stems.get("bass", bass_wav)
        guitar_wav = stems.get("guitar", guitar_wav)
    else:
        print("\n[Step 1/4] Skipping separation (existing stems found).")

    # -------------------------------------------------------------
    # Step 2: Split Guitar into Lead and Rhythm
    # -------------------------------------------------------------
    print("\n[Step 2/4] Splitting Guitar into Lead and Rhythm (M/S + Spectral)...")
    if os.path.exists(guitar_wav):
        guitar_parts = split_guitar_track(guitar_wav, stems_dir)
        lead_wav = guitar_parts["lead"]
        rhythm_wav = guitar_parts["rhythm"]
        print(f"   ✓ Lead Guitar Audio:   {lead_wav}")
        print(f"   ✓ Rhythm Guitar Audio: {rhythm_wav}")
    else:
        print("   [!] Warning: guitar.wav not found. Skipping guitar splitting.")
        lead_wav, rhythm_wav = None, None

    # -------------------------------------------------------------
    # Step 3: Transcription to MIDI
    # -------------------------------------------------------------
    print("\n[Step 3/4] Transcribing audio stems to MIDI tracks (SOTA Neural & Chroma Engine)...")
    
    # 1. Drums
    drum_midi = os.path.join(midi_dir, "drums.mid")
    if 'drums' in selected_parts and os.path.exists(drums_wav):
        print("   - Transcribing Drums (Kick, Snare, Hi-Hat with adaptive onset)...")
        transcribe_drums(drums_wav, drum_midi)
        print(f"   ✓ Drum MIDI: {drum_midi}")
        
    # 2. Bass
    bass_midi = os.path.join(midi_dir, "bass.mid")
    if 'bass' in selected_parts and os.path.exists(bass_wav):
        print("   - Transcribing Bass (TorchCREPE GPU 360-bin Neural F0 tracking)...")
        transcribe_pitch(bass_wav, bass_midi, instrument_name="bass")
        print(f"   ✓ Bass MIDI: {bass_midi}")

    # 3. Lead Guitar
    lead_midi = os.path.join(midi_dir, "lead_guitar.mid")
    if 'lead' in selected_parts and lead_wav and os.path.exists(lead_wav):
        print("   - Transcribing Lead Guitar (TorchCREPE GPU Solo & Melody tracking)...")
        transcribe_pitch(lead_wav, lead_midi, instrument_name="guitar_lead")
        print(f"   ✓ Lead Guitar MIDI: {lead_midi}")

    # 4. Rhythm Guitar
    rhythm_midi = os.path.join(midi_dir, "rhythm_guitar.mid")
    if 'rhythm' in selected_parts and rhythm_wav and os.path.exists(rhythm_wav):
        print("   - Transcribing Rhythm Guitar (CQT Chroma chord recognition & 6-string voicings)...")
        transcribe_pitch(rhythm_wav, rhythm_midi, instrument_name="guitar_rhythm")
        print(f"   ✓ Rhythm Guitar MIDI: {rhythm_midi}")

    # -------------------------------------------------------------
    # Step 4: Tablature Optimization & MusicXML Generation
    # -------------------------------------------------------------
    print("\n[Step 4/4] Optimizing Guitar/Bass Fretboard & Generating MusicXML Score...")
    
    # Dynamic BPM detection
    import librosa
    try:
        y_bpm, sr_bpm = librosa.load(input_path, sr=22050, duration=45.0)
        detected_tempo, _ = librosa.beat.beat_track(y=y_bpm, sr=sr_bpm)
        bpm_val = float(detected_tempo)
        if hasattr(bpm_val, '__len__'):
            bpm_val = float(bpm_val[0])
        if bpm_val < 50:
            bpm_val = 120.0
        elif bpm_val > 220:
            bpm_val = bpm_val / 2.0
    except Exception:
        bpm_val = 120.0
    print(f"   ✓ Detected Tempo: {bpm_val:.1f} BPM (Precision measure sync)")

    part_suffix = "" if len(selected_parts) == 4 else f"_{'_'.join(selected_parts)}"
    full_score_xml = os.path.join(score_dir, f"{song_title}{part_suffix}_score.musicxml")
    
    build_musicxml_score(
        drum_midi_path=drum_midi if 'drums' in selected_parts else "",
        bass_midi_path=bass_midi if 'bass' in selected_parts else "",
        lead_midi_path=lead_midi if 'lead' in selected_parts else "",
        rhythm_midi_path=rhythm_midi if 'rhythm' in selected_parts else "",
        output_xml_path=full_score_xml,
        song_title=song_title,
        selected_parts=selected_parts,
        bpm=bpm_val
    )
    print(f"   ✓ Custom Score (MusicXML): {full_score_xml}")

    # Headless PDF Export
    pdf_path = export_score_to_pdf(full_score_xml, style=style)
    if pdf_path:
        print(f"   ✓ Score PDF ({style}):     {pdf_path}")

    elapsed = time.time() - start_time
    print("\n" + "=" * 65)
    print(f"   COMPLETED in {elapsed:.1f} seconds!")
    print("=" * 65)
    print(f"All files generated successfully:")
    print(f"  [Stems]   : {stems_dir}")
    print(f"  [MIDIs]   : {midi_dir}")
    print(f"  [XML]     : {full_score_xml}")
    if pdf_path:
        print(f"  [PDF]     : {pdf_path}")
    print("\nYou can open the generated .musicxml or .pdf file directly!")


def main():
    parser = argparse.ArgumentParser(description="AI Band Transcriber (Drums, Bass, Lead, Rhythm -> TAB & Score)")
    parser.add_argument("-i", "--input", required=True, help="Path to input audio file or YouTube URL")
    parser.add_argument("-o", "--output", default="output", help="Output directory path (default: ./output)")
    parser.add_argument("--skip-separation", action="store_true", help="Skip stem separation if stems already exist")
    parser.add_argument("--parts", default="drums,bass,rhythm,lead", help="Comma-separated parts to include (e.g. 'lead,bass' or 'rhythm')")
    parser.add_argument("--style", default="tab", choices=["tab", "standard", "both"], help="PDF style: tab, standard, both")
    args = parser.parse_args()

    selected_parts = [p.strip() for p in args.parts.split(",") if p.strip()]
    run_pipeline(args.input, args.output, skip_separation=args.skip_separation, parts=selected_parts, style=args.style)


if __name__ == "__main__":
    main()
