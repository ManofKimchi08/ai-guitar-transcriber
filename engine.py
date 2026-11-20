"""
AI Band Transcriber - Local Engine
Runs this PC's transcription pipeline for the web page: start it, open the page, and
enter the pairing code shown here once per browser. Results go to the same output
folder the desktop GUI uses.
"""

import argparse
import os
import sys
import time
import webbrowser

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from src import __version__
from src.engine_server import DEFAULT_ORIGINS, DEFAULT_PORT, SITE_URL, Engine, EngineServer, default_runner


def console_runner(input_audio, output_dir, log=print, **options):
    """The pipeline, with its log also shown in this window."""
    def tee(line):
        print(line, flush=True)
        log(line)
    print(f"\n[*] 변환 시작: {os.path.basename(input_audio)}", flush=True)
    return default_runner(input_audio, output_dir, log=tee, **options)


def main():
    parser = argparse.ArgumentParser(description="AI Band Transcriber local engine for the web page")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port on 127.0.0.1 (default: {DEFAULT_PORT})")
    parser.add_argument("-o", "--output", default=os.path.join(PROJECT_DIR, "output"),
                        help="Output root folder, shared with the desktop GUI (default: ./output)")
    parser.add_argument("--site", default=SITE_URL, help="Web page opened on start")
    parser.add_argument("--allow-origin", action="append", default=[],
                        help="Extra site origin allowed to use the engine, e.g. https://example.com")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the web page on start")
    parser.add_argument("--reset-token", action="store_true", help="Unpair every browser paired so far")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):  # a console or pipe without Korean must not stop the engine
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    engine = Engine(args.output, os.path.join(PROJECT_DIR, "engine_data"),
                    origins=list(DEFAULT_ORIGINS) + args.allow_origin, runner=console_runner)
    if args.reset_token:
        engine.reset_token()
        print("[*] 연결을 초기화했습니다. 브라우저마다 연결 코드를 다시 입력해야 합니다.")
    engine.on_pair_code = lambda code: print(f"\n[*] 새 연결 코드: {code}", flush=True)
    try:
        server = EngineServer(engine, args.port)
    except OSError as e:
        print(f"[ERROR] 127.0.0.1:{args.port} 포트를 열 수 없습니다 ({e}).")
        print("        엔진이 이미 켜져 있는지 확인하세요. 다른 포트는 --port 로 지정합니다.")
        sys.exit(1)

    print("=" * 56)
    print(f"  AI Band Transcriber 엔진 v{__version__}")
    print("=" * 56)
    print(f"  주소     : http://127.0.0.1:{args.port}")
    print(f"  결과 폴더: {engine.output_root}")
    print(f"  연결 코드: {engine.pair_code}   (웹페이지에서 처음 한 번 입력)")
    print("  끄려면 이 창을 닫거나 Ctrl+C를 누르세요.")
    print("=" * 56, flush=True)
    if not args.no_browser:
        # The page reads the code from the address and pairs by itself; the code works once.
        webbrowser.open(f"{args.site}#pair={engine.pair_code}&port={args.port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] 엔진을 끕니다...")
    finally:
        engine.cancel_all()
        time.sleep(0.5)  # let a running Demucs see the cancel and be killed
        server.server_close()


if __name__ == "__main__":
    main()
