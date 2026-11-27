import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kaggle_runner


class KaggleRunnerTests(unittest.TestCase):
    def test_main_submits_text_to_video_queue_without_kaggle_dependencies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workflow = {
                "6": {"inputs": {"text": ""}},
                "70": {"inputs": {"width": 512, "height": 512, "length": 97}},
                "72": {"inputs": {"noise_seed": 1}},
            }
            queue = {
                "workflow_nodes_by_mode": {
                    "text-to-video": {
                        "positive_prompt": "6",
                        "width": "70",
                        "height": "70",
                        "frames": "70",
                        "seed": "72",
                        "seed_input": "noise_seed",
                    }
                },
                "defaults": {"width": 512, "height": 512, "frames": 17},
                "prompts": [{"mode": "text-to-video", "prompt": "sunrise over mountains"}],
            }
            (root / "prompts_queue.json").write_text(json.dumps(queue), encoding="utf-8")
            (root / "workflow_text_to_video_api.json").write_text(
                json.dumps(workflow), encoding="utf-8"
            )
            submissions = []

            def fake_request(path, payload=None):
                submissions.append((path, payload))
                return {"prompt_id": "offline-test"} if path == "/prompt" else {}

            with (
                patch.object(kaggle_runner, "QUEUE_PATH", root / "prompts_queue.json"),
                patch.object(kaggle_runner, "COMFY_DIR", root),
                patch.object(kaggle_runner, "wait_for_server"),
                patch.object(kaggle_runner, "wait_for_prompt"),
                patch.object(kaggle_runner, "request_json", side_effect=fake_request),
            ):
                kaggle_runner.main()

            self.assertEqual(len(submissions), 1)
            prompt = submissions[0][1]["prompt"]
            self.assertEqual(prompt["6"]["inputs"]["text"], "sunrise over mountains")
            self.assertEqual(prompt["70"]["inputs"]["length"], 17)

    def test_text_to_video_mode_loads_its_api_workflow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            comfy_dir = Path(temp_dir)
            expected = {"1": {"inputs": {"text": ""}}}
            (comfy_dir / "workflow_text_to_video_api.json").write_text(
                json.dumps(expected), encoding="utf-8"
            )
            with patch.object(kaggle_runner, "COMFY_DIR", comfy_dir):
                self.assertEqual(kaggle_runner.load_workflow("text-to-video"), expected)

    def test_video_workflow_maps_source_image_prompt_and_frame_settings(self):
        workflow = {
            "1": {"inputs": {"text": ""}},
            "2": {"inputs": {"image": "old.png"}},
            "3": {"inputs": {"filename_prefix": "old"}},
            "4": {"inputs": {"width": 512, "height": 512, "length": 17}},
        }
        queue = {
            "workflow_nodes_by_mode": {
                "image-to-video": {
                    "positive_prompt": "1",
                    "source_image": "2",
                    "filename_prefix": "3",
                    "width": "4",
                    "height": "4",
                    "frames": "4",
                }
            },
            "defaults": {"width": 640, "height": 384, "frames": 17},
            "prompts": [{"mode": "image-to-video", "prompt": "gentle movement"}],
        }
        defaults, nodes, _ = kaggle_runner.normalize_queue(queue)

        result = kaggle_runner.prepare_prompt(
            workflow,
            {"mode": "image-to-video", "prompt": "gentle camera movement"},
            defaults,
            nodes,
            1,
            "source.png",
        )

        self.assertEqual(result["1"]["inputs"]["text"], "gentle camera movement")
        self.assertEqual(result["2"]["inputs"]["image"], "source.png")
        self.assertEqual(result["3"]["inputs"]["filename_prefix"], "batch_0001_gentle_camera_movement")
        self.assertEqual(result["4"]["inputs"], {"width": 640, "height": 384, "length": 17})

    def test_text_to_video_workflow_maps_prompt_and_frame_settings_without_image(self):
        workflow = {
            "1": {"inputs": {"text": ""}},
            "2": {"inputs": {"width": 512, "height": 512, "length": 97}},
            "3": {"inputs": {"noise_seed": 1}},
        }
        queue = {
            "workflow_nodes_by_mode": {
                "text-to-video": {
                    "positive_prompt": "1",
                    "width": "2",
                    "height": "2",
                    "frames": "2",
                    "seed": "3",
                    "seed_input": "noise_seed",
                }
            },
            "defaults": {"width": 512, "height": 512, "frames": 17},
            "prompts": [{"mode": "text-to-video", "prompt": "clouds drift over hills"}],
        }
        defaults, nodes, prompts = kaggle_runner.normalize_queue(queue)

        result = kaggle_runner.prepare_prompt(
            workflow, prompts[0], defaults, nodes, 1
        )

        self.assertEqual(result["1"]["inputs"]["text"], "clouds drift over hills")
        self.assertEqual(result["2"]["inputs"], {"width": 512, "height": 512, "length": 17})
        self.assertEqual(result["3"]["inputs"]["noise_seed"], 1)

    def test_input_image_is_copied_from_jobs_dataset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs = root / "jobs"
            comfy = root / "ComfyUI"
            (jobs / "inputs").mkdir(parents=True)
            (jobs / "inputs" / "source.png").write_bytes(b"image")

            with (
                patch.object(kaggle_runner, "JOBS_INPUT_DIR", jobs),
                patch.object(kaggle_runner, "COMFY_DIR", comfy),
            ):
                name = kaggle_runner.stage_input_image("inputs/source.png")

            self.assertEqual(name, "source.png")
            self.assertEqual((comfy / "input" / name).read_bytes(), b"image")

    def test_input_image_cannot_escape_jobs_dataset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(kaggle_runner, "JOBS_INPUT_DIR", root / "jobs"):
                with self.assertRaisesRegex(RuntimeError, "must stay inside"):
                    kaggle_runner.stage_input_image("../outside.png")


if __name__ == "__main__":
    unittest.main()
