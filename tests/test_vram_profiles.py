"""
Tests for the VRAM 8GB/4GB profiles: profile detection, and that each memory knob
(Demucs --segment, TorchCREPE batch size) is applied and retried on GPU OOM.
"""

import os
import sys
import subprocess
import unittest
from unittest import mock

import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import src.separator as separator
from src.pipeline import VRAM_PROFILES, vram_profile_for
from src.pitch_transcriber import transcribe_with_crepe


class TestVramProfiles(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_output", "vram"))
        os.makedirs(self.test_dir, exist_ok=True)

    def test_profile_for_detected_memory(self):
        # "8 GB" cards report a little under 8 GiB
        for total_gb in (7.6, 8.0, 11.9, 24.0):
            self.assertEqual(vram_profile_for(total_gb), "8gb", total_gb)
        for total_gb in (3.9, 4.0, 5.8):
            self.assertEqual(vram_profile_for(total_gb), "4gb", total_gb)

    def test_low_profile_uses_less_memory(self):
        low, std = VRAM_PROFILES["4gb"], VRAM_PROFILES["8gb"]
        self.assertLess(low["crepe_batch_size"], std["crepe_batch_size"])
        self.assertIsNotNone(low["demucs_segment"])
        self.assertLessEqual(low["demucs_segment"], 7)  # Hybrid Transformer max is 7.8 s

    def _fake_demucs_output(self, out_dir):
        track = os.path.join(out_dir, "htdemucs_6s", "song")
        os.makedirs(track, exist_ok=True)
        sf.write(os.path.join(track, "guitar.wav"), np.zeros(100), 22050)

    def test_demucs_segment_and_oom_retry(self):
        out_dir = os.path.join(self.test_dir, "demucs")
        self._fake_demucs_output(out_dir)
        calls = []

        def fake_run(cmd, env=None, **kwargs):
            calls.append((cmd, env))
            if len(calls) == 1:
                raise subprocess.CalledProcessError(1, cmd, output="torch.OutOfMemoryError: CUDA out of memory.")
            return ""

        with mock.patch.object(separator, "run_streaming", side_effect=fake_run):
            stems = separator.separate_stems(os.path.join(out_dir, "song.wav"), out_dir,
                                             device="cuda", segment=5)

        self.assertIn("guitar", stems)
        first_cmd, first_env = calls[0]
        self.assertEqual(first_cmd[first_cmd.index("--segment") + 1], "5")
        self.assertIsNone(first_env)
        retry_cmd, retry_env = calls[1]
        self.assertEqual(retry_cmd[retry_cmd.index("--segment") + 1], str(separator.LOW_MEMORY_SEGMENT))
        self.assertEqual(retry_env["PYTORCH_NO_CUDA_MEMORY_CACHING"], "1")

    def test_demucs_default_segment_and_other_errors(self):
        out_dir = os.path.join(self.test_dir, "demucs_err")
        self._fake_demucs_output(out_dir)
        calls = []

        def failing_run(cmd, env=None, **kwargs):
            calls.append(cmd)
            raise subprocess.CalledProcessError(1, cmd, output="Some other failure")

        with mock.patch.object(separator, "run_streaming", side_effect=failing_run):
            with self.assertRaises(RuntimeError):
                separator.separate_stems(os.path.join(out_dir, "song.wav"), out_dir, device="cuda")

        self.assertEqual(len(calls), 1)  # only GPU OOM is retried
        self.assertNotIn("--segment", calls[0])  # None keeps Demucs's own default

    def test_streaming_runner_relays_progress_and_stops_hung_processes(self):
        child = (
            "import sys, time\n"
            "print('Separating track song.wav', flush=True)\n"
            "for p in range(0, 101, 5):\n"
            "    sys.stderr.write(f'\\r {p}%|####| {p}/100 [00:01<00:00]'); sys.stderr.flush(); time.sleep(0.01)\n"
            "sys.stderr.write('\\n')\n"
        )
        logs, fractions = [], []
        output = separator.run_streaming([sys.executable, "-c", child], log=logs.append, progress=fractions.append)
        self.assertIn("Separating track song.wav", output)
        self.assertEqual(fractions[0], 0.0)
        self.assertEqual(fractions[-1], 1.0)
        self.assertEqual(sum("진행률" in line for line in logs), 11)  # every 10%, not every update
        self.assertIn("   Separating track song.wav", logs)

        hung = [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"]
        with self.assertRaises(subprocess.CalledProcessError) as ctx:
            separator.run_streaming(hung, log=lambda m: None, idle_timeout=2)
        self.assertIn("stopped", ctx.exception.output)

    def test_crepe_batch_size_and_oom_retry(self):
        sr = 16000
        t = np.arange(sr) / sr
        wav = os.path.join(self.test_dir, "a3.wav")
        sf.write(wav, 0.5 * np.sin(2 * np.pi * 220.0 * t), sr)
        batches = []

        def fake_predict(x, batch_size, **kwargs):
            batches.append(batch_size)
            if len(batches) == 1:
                raise torch.cuda.OutOfMemoryError("CUDA out of memory")
            frames = 1 + x.shape[-1] // kwargs["hop_length"]
            return torch.full((1, frames), 220.0), torch.ones(1, frames)

        with mock.patch("torchcrepe.predict", side_effect=fake_predict):
            transcribe_with_crepe(wav, os.path.join(self.test_dir, "a3.mid"), instrument_name="guitar_lead",
                                  device="cpu", batch_size=512)

        self.assertEqual(batches, [512, 128])


if __name__ == "__main__":
    unittest.main()
