"""
YouTube Audio Downloader Module
Downloads the highest quality audio stream from a YouTube URL and converts it to WAV using yt-dlp and FFmpeg.
"""

import os
import re
import yt_dlp


def sanitize_filename(name: str) -> str:
    """Sanitizes a string to be a safe filename across operating systems."""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def download_youtube_audio(url: str, output_dir: str, progress_hook=None) -> tuple:
    """
    Downloads audio from a YouTube URL and returns the local WAV file path and video title.
    
    Args:
        url: YouTube video URL.
        output_dir: Directory where the audio file will be saved.
        progress_hook: Optional callable hook for download progress.
        
    Returns:
        tuple: (downloaded_wav_path, video_title)
    """
    os.makedirs(output_dir, exist_ok=True)

    # Temporary template
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
    }

    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'youtube_audio')
        safe_title = sanitize_filename(title)
        
        # Determine output file path
        wav_path = os.path.join(output_dir, f"{safe_title}.wav")
        if not os.path.exists(wav_path):
            # Fallback search if title formatting differs slightly
            candidates = [
                os.path.join(output_dir, f)
                for f in os.listdir(output_dir)
                if f.endswith('.wav')
            ]
            if candidates:
                # Return newest file
                wav_path = max(candidates, key=os.path.getctime)

        return wav_path, title


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        path, title = download_youtube_audio(sys.argv[1], sys.argv[2])
        print(f"Downloaded: {title} -> {path}")
