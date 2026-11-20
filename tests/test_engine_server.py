"""
Tests for the local engine server the web page talks to, with a fake pipeline: who may
call it (host, origin, pairing token), uploads, a job's stages, log and cancel, and the
finished songs and their files.
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import quote

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.cancellation import PipelineCancelled
from src.engine_server import PAIR_ATTEMPTS, Engine, EngineServer, safe_file_name

SITE = "https://manofkimchi08.github.io"


def fake_pipeline(input_audio, output_dir, parts=None, style="tab", vram=None, lyrics=True,
                  lyrics_language=None, log=print, progress=None, stage=None, cancel=None):
    """Announces a file run's stages and writes a score, its PDF and a MIDI like the pipeline."""
    title = os.path.splitext(os.path.basename(input_audio))[0]
    for i, key in enumerate(["separate", "split", "transcribe", "score"]):
        if cancel.is_set():
            raise PipelineCancelled()
        stage(key)
        progress(f"{key}...", 0.25 * i)
        log(f"[*] {key}")
    song_dir = os.path.join(output_dir, title)
    score = os.path.join(song_dir, "scores", f"{title}_score.musicxml")
    os.makedirs(os.path.dirname(score), exist_ok=True)
    os.makedirs(os.path.join(song_dir, "midi"), exist_ok=True)
    for path, data in ((score, "<score-partwise/>"), (score.replace(".musicxml", "_TAB.pdf"), "%PDF-1.4"),
                       (os.path.join(song_dir, "midi", "bass.mid"), "MThd")):
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
    return {"song_title": title, "song_dir": song_dir, "score_xml": score,
            "pdf_path": score.replace(".musicxml", "_TAB.pdf"), "lyrics_txt": None, "elapsed": 0.1}


class Blocking:
    """A pipeline that stays in its first stage until cancelled (or released)."""

    def __init__(self):
        self.release = threading.Event()

    def __call__(self, input_audio, output_dir, log=print, progress=None, stage=None, cancel=None, **options):
        stage("separate")
        while not cancel.is_set():
            if self.release.is_set():
                return {}
            log("   working")
            time.sleep(0.01)
        raise PipelineCancelled()


def failing_pipeline(input_audio, output_dir, stage=None, **options):
    stage("separate")
    raise RuntimeError("CUDA out of memory")


class EngineTestCase(unittest.TestCase):
    runner = staticmethod(fake_pipeline)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = os.path.join(self.tmp.name, "output")
        self.engine = Engine(self.output, os.path.join(self.tmp.name, "engine_data"),
                             runner=self.runner, gpu_detector=lambda: {"name": "Test GPU", "vram_gb": 8.0})
        self.server = EngineServer(self.engine, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.token = None

    def request(self, method, path, body=None, headers=None, origin=SITE, auth=True, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        hdrs = {"Origin": origin} if origin else {}
        if auth and self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn.request(method, path, body=body, headers=hdrs)
        res = conn.getresponse()
        data = res.read()
        if raw:
            return res, data
        return res, (json.loads(data) if data else None)

    def pair(self):
        res, data = self.request("POST", "/api/pair", {"code": self.engine.pair_code})
        self.assertEqual(res.status, 200)
        self.token = data["token"]

    def upload(self, name="My Song.mp3", data=b"ID3fake"):
        return self.request("POST", "/api/uploads", data, {"X-File-Name": quote(name)})

    def wait_job(self, job_id, until=("done", "error", "cancelled")):
        for _ in range(500):
            res, job = self.request("GET", f"/api/jobs/{job_id}")
            if job["state"] in until:
                return job
            time.sleep(0.01)
        self.fail(f"job stuck: {job}")


class TestAccess(EngineTestCase):
    def test_status_needs_no_token_and_reports_the_gpu(self):
        for _ in range(100):
            if self.engine.gpu_checked:
                break
            time.sleep(0.01)
        res, status = self.request("GET", "/api/status")
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader("Access-Control-Allow-Origin"), SITE)
        self.assertEqual(status["app"], "ai-band-transcriber")
        self.assertFalse(status["paired"])
        self.assertEqual(status["gpu"], {"name": "Test GPU", "vram_gb": 8.0})
        self.assertIn("vocals", status["parts"])

    def test_other_sites_and_hosts_are_refused(self):
        res, _ = self.request("GET", "/api/status", origin="https://evil.example")
        self.assertEqual(res.status, 403)
        self.assertIsNone(res.getheader("Access-Control-Allow-Origin"))
        # DNS rebinding: a page on another name resolving to 127.0.0.1
        res, _ = self.request("GET", "/api/status", headers={"Host": f"evil.example:{self.port}"})
        self.assertEqual(res.status, 403)
        # a page served from this PC (developing the web page) is allowed
        res, _ = self.request("GET", "/api/status", origin="http://localhost:5173")
        self.assertEqual(res.status, 200)

    def test_preflight_allows_the_site_and_private_network(self):
        res, _ = self.request("OPTIONS", "/api/jobs", headers={
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Private-Network": "true"})
        self.assertEqual(res.status, 204)
        self.assertEqual(res.getheader("Access-Control-Allow-Origin"), SITE)
        self.assertEqual(res.getheader("Access-Control-Allow-Private-Network"), "true")
        self.assertIn("Authorization", res.getheader("Access-Control-Allow-Headers"))
        res, _ = self.request("OPTIONS", "/api/jobs", origin="https://evil.example")
        self.assertEqual(res.status, 403)

    def test_everything_else_needs_the_pairing_token(self):
        for method, path in (("GET", "/api/songs"), ("POST", "/api/jobs"), ("GET", "/api/jobs/current")):
            res, data = self.request(method, path, {} if method == "POST" else None)
            self.assertEqual(res.status, 401, path)
            self.assertIn("연결 코드", data["error"])
        self.token = "x" * 43
        res, _ = self.request("GET", "/api/songs")
        self.assertEqual(res.status, 401)

    def test_pairing_code_works_once_and_rotates_after_wrong_guesses(self):
        code = self.engine.pair_code
        self.pair()
        res, status = self.request("GET", "/api/status")
        self.assertTrue(status["paired"])
        res, _ = self.request("POST", "/api/pair", {"code": code}, auth=False)
        self.assertEqual(res.status, 403)  # already used
        code = self.engine.pair_code
        for _ in range(PAIR_ATTEMPTS):
            self.request("POST", "/api/pair", {"code": "000000" if code != "000000" else "111111"})
        self.assertNotEqual(self.engine.pair_code, code)

    def test_token_survives_a_restart_until_reset(self):
        self.pair()
        again = Engine(self.output, os.path.join(self.tmp.name, "engine_data"), runner=fake_pipeline,
                       gpu_detector=lambda: None)
        self.assertEqual(again.token, self.token)
        again.reset_token()
        self.assertNotEqual(again.token, self.token)


class TestJobs(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.pair()

    def test_upload_run_and_fetch_the_files(self):
        res, up = self.upload("../My Song.mp3")
        self.assertEqual(res.status, 201)
        self.assertEqual(up["name"], "My Song.mp3")
        res, job = self.request("POST", "/api/jobs", {"upload_id": up["upload_id"], "parts": ["bass", "drums"],
                                                       "style": "both", "lyrics_language": "ko"})
        self.assertEqual(res.status, 202)
        self.assertEqual(job["options"]["parts"], ["drums", "bass"])
        job = self.wait_job(job["id"])
        self.assertEqual(job["state"], "done", job)
        self.assertEqual(job["stages"], ["separate", "split", "transcribe", "score"])
        self.assertEqual(job["fraction"], 1.0)
        self.assertIn("[*] score", job["log"])
        self.assertEqual(job["result"]["song"], "My Song")
        self.assertEqual(job["result"]["pdf"], "scores/My Song_score_TAB.pdf")

        res, later = self.request("GET", f"/api/jobs/{job['id']}?log_from={job['log_next'] - 1}")
        self.assertEqual(later["log"], job["log"][-1:])

        res, songs = self.request("GET", "/api/songs")
        self.assertEqual([s["name"] for s in songs], ["My Song"])
        self.assertEqual(songs[0]["tags"], ["TAB"])
        self.assertIn({"path": "midi/bass.mid", "size": 4}, songs[0]["files"])

        res, data = self.request("GET", "/api/songs/" + quote("My Song") + "/files/" + quote(songs[0]["pdf"]), raw=True)
        self.assertEqual(res.status, 200)
        self.assertEqual(data, b"%PDF-1.4")
        self.assertEqual(res.getheader("Content-Type"), "application/pdf")
        self.assertIn("filename*=UTF-8''My%20Song_score_TAB.pdf", res.getheader("Content-Disposition"))

    def test_files_outside_the_song_folders_are_not_served(self):
        _, up = self.upload()
        _, job = self.request("POST", "/api/jobs", {"upload_id": up["upload_id"]})
        self.wait_job(job["id"])
        with open(os.path.join(self.output, "My Song", "secret.txt"), "w") as f:
            f.write("x")
        for rel in ("secret.txt", "scores/../secret.txt", "scores/%2E%2E/secret.txt", "../../engine_data/engine.json"):
            res, _ = self.request("GET", "/api/songs/My%20Song/files/" + rel)
            self.assertEqual(res.status, 404, rel)
        res, _ = self.request("GET", "/api/songs/..%2Foutput/files/scores/x")
        self.assertEqual(res.status, 404)

    def test_bad_requests_are_refused_with_a_message(self):
        res, data = self.upload("notes.txt")
        self.assertEqual(res.status, 400)
        self.assertIn("형식", data["error"])
        _, up = self.upload()
        for body in ({"upload_id": up["upload_id"], "parts": ["piano"]},
                     {"upload_id": up["upload_id"], "style": "pdf"},
                     {"upload_id": up["upload_id"], "vram": "16gb"},
                     {"upload_id": up["upload_id"], "lyrics_language": "korean!"},
                     {"upload_id": "../../etc"},
                     {"url": "file:///C:/Windows"}):
            res, data = self.request("POST", "/api/jobs", body)
            self.assertEqual(res.status, 400, body)
            self.assertTrue(data["error"])
        res, data = self.request("POST", "/api/jobs", {"upload_id": "0123456789ab"})
        self.assertEqual(res.status, 404)
        # the connection stays usable after a refused upload body
        res, _ = self.request("GET", "/api/status")
        self.assertEqual(res.status, 200)

    def test_a_link_is_passed_to_the_pipeline(self):
        seen = []
        self.engine.runner = lambda input_audio, output_dir, **options: seen.append(input_audio) or {}
        _, job = self.request("POST", "/api/jobs", {"url": " https://youtu.be/abc "})
        self.assertEqual(self.wait_job(job["id"])["state"], "done")
        self.assertEqual(seen, ["https://youtu.be/abc"])

    def test_an_error_is_reported(self):
        self.engine.runner = failing_pipeline
        _, up = self.upload()
        _, job = self.request("POST", "/api/jobs", {"upload_id": up["upload_id"]})
        job = self.wait_job(job["id"])
        self.assertEqual(job["state"], "error")
        self.assertEqual(job["error"], "CUDA out of memory")
        self.assertEqual(job["stage"], "separate")


class TestCancel(EngineTestCase):
    runner = Blocking()

    def setUp(self):
        type(self).runner = Blocking()
        super().setUp()
        self.pair()

    def test_one_job_at_a_time_and_cancel(self):
        _, up = self.upload()
        res, job = self.request("POST", "/api/jobs", {"upload_id": up["upload_id"]})
        self.assertEqual(res.status, 202)
        self.wait_job(job["id"], until=("running",))
        res, data = self.request("POST", "/api/jobs", {"upload_id": up["upload_id"]})
        self.assertEqual(res.status, 409)
        res, status = self.request("GET", "/api/status")
        self.assertTrue(status["busy"])
        self.assertEqual(status["job_id"], job["id"])
        res, current = self.request("GET", "/api/jobs/current")
        self.assertEqual(current["id"], job["id"])

        res, _ = self.request("POST", f"/api/jobs/{job['id']}/cancel")
        self.assertEqual(res.status, 200)
        job = self.wait_job(job["id"])
        self.assertEqual(job["state"], "cancelled")
        self.assertIn("취소", job["log"][-1])
        # the next song can start
        res, _ = self.request("POST", "/api/jobs", {"upload_id": up["upload_id"]})
        self.assertEqual(res.status, 202)
        self.engine.runner.release.set()


class TestNames(unittest.TestCase):
    def test_safe_file_name(self):
        self.assertEqual(safe_file_name("C:\\Users\\me\\노래 (Live).MP3"), "노래 (Live).mp3")
        self.assertEqual(safe_file_name('a<b>c:"d|e?.wav'), "a_b_c__d_e_.wav")
        self.assertEqual(safe_file_name("song.mp3. "), "song.mp3")
        self.assertEqual(safe_file_name(".mp3"), "song.mp3")


if __name__ == "__main__":
    unittest.main()
