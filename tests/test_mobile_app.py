import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mobile_app


class MobileAppTests(unittest.TestCase):
    def test_parse_node_mappings_accepts_mode_specific_fields(self):
        mappings = mobile_app.parse_node_mappings(json.dumps({
            "image-to-video": {
                "positive_prompt": 6,
                "source_image": "10",
                "frames": 13,
                "frames_input": "video_frames",
            }
        }))

        self.assertEqual(mappings["image-to-video"]["positive_prompt"], "6")
        self.assertEqual(mappings["image-to-video"]["source_image"], "10")
        self.assertEqual(mappings["image-to-video"]["frames_input"], "video_frames")

    def test_parse_node_mappings_accepts_text_to_video(self):
        mappings = mobile_app.parse_node_mappings(json.dumps({
            "text-to-video": {
                "positive_prompt": "6",
                "seed": "72",
                "seed_input": "noise_seed",
                "frames": "70",
            }
        }))

        self.assertEqual(mappings["text-to-video"]["seed_input"], "noise_seed")

    def test_parse_node_mappings_rejects_unknown_mode_or_key(self):
        with self.assertRaisesRegex(ValueError, "Unsupported workflow mapping mode"):
            mobile_app.parse_node_mappings('{"invalid-video": {}}')
        with self.assertRaisesRegex(ValueError, "Unsupported workflow node mapping"):
            mobile_app.parse_node_mappings('{"text-to-image": {"secret": "1"}}')

    def test_video_submission_versions_only_jobs_dataset_and_attaches_both_datasets(self):
        node_mappings = json.dumps({
            "image-to-video": {
                "positive_prompt": "1",
                "source_image": "2",
                "frames": "3",
                "width": "4",
                "height": "4",
            }
        })
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kernel_dir = root / "kernel"
            kernel_dir.mkdir()
            (kernel_dir / "kernel-metadata.json").write_text(json.dumps({
                "id": "user/comfyui-batch",
                "code_file": "main.py",
                "dataset_sources": [],
            }))
            (kernel_dir / "main.py").write_text("print('kernel')")
            source_image = root / "source.png"
            source_image.write_bytes(b"test image")
            calls = []
            jobs_dataset_is_private = True

            def fake_run_kaggle(args, timeout=600):
                calls.append(args)
                if args[:2] == ["datasets", "metadata"]:
                    staging = Path(args[args.index("-p") + 1])
                    (staging / "dataset-metadata.json").write_text(json.dumps({
                        "id": "user/comfyui-jobs",
                        "licenses": [{"name": "CC0-1.0"}],
                        "isPrivate": jobs_dataset_is_private,
                    }))
                elif args[:2] == ["datasets", "download"]:
                    self.assertEqual(args[args.index("-d") + 1], "user/comfyui-jobs")
                elif args[:2] == ["datasets", "version"]:
                    staging = Path(args[args.index("-p") + 1])
                    queue = json.loads((staging / "prompts_queue.json").read_text())
                    self.assertEqual(queue["prompts"][0]["mode"], "image-to-video")
                    self.assertEqual(queue["prompts"][0]["input_image"].split("/")[0], "inputs")
                    self.assertTrue((staging / queue["prompts"][0]["input_image"]).is_file())
                    return "dataset version created"
                elif args[:2] == ["kernels", "push"]:
                    staging = Path(args[args.index("-p") + 1])
                    metadata = json.loads((staging / "kernel-metadata.json").read_text())
                    self.assertEqual(metadata["id"], "user/comfyui-batch")
                    self.assertEqual(
                        metadata["dataset_sources"],
                        ["user/comfyui-assets", "user/comfyui-jobs"],
                    )
                    self.assertTrue(metadata["enable_gpu"])
                    self.assertTrue(metadata["is_private"])
                    return "kernel pushed"
                return "ok"

            with patch.object(mobile_app, "run_kaggle") as run_kaggle_mock:
                with self.assertRaisesRegex(ValueError, "frame count"):
                    list(mobile_app.update_dataset_and_push(
                        "user/comfyui-assets", "user/comfyui-jobs",
                        "user/comfyui-batch", str(kernel_dir), "image-to-video",
                        "gentle camera movement", "", "", str(source_image),
                        512, 512, 14, node_mappings,
                    ))
                with self.assertRaisesRegex(ValueError, "multiples of 32"):
                    list(mobile_app.update_dataset_and_push(
                        "user/comfyui-assets", "user/comfyui-jobs",
                        "user/comfyui-batch", str(kernel_dir), "image-to-video",
                        "gentle camera movement", "", "", str(source_image),
                        512, 360, 17, node_mappings,
                    ))
                run_kaggle_mock.assert_not_called()

            jobs_dataset_is_private = False
            calls_before_privacy_check = len(calls)
            with patch.object(mobile_app, "run_kaggle", side_effect=fake_run_kaggle):
                with self.assertRaisesRegex(RuntimeError, "not confirmed private"):
                    list(mobile_app.update_dataset_and_push(
                        "user/comfyui-assets", "user/comfyui-jobs",
                        "user/comfyui-batch", str(kernel_dir), "image-to-video",
                        "gentle camera movement", "", "", str(source_image),
                        512, 512, 17, node_mappings,
                    ))
            self.assertEqual(len(calls), calls_before_privacy_check + 1)
            jobs_dataset_is_private = True

            with patch.object(mobile_app, "run_kaggle", side_effect=fake_run_kaggle):
                updates = list(mobile_app.update_dataset_and_push(
                    "user/comfyui-assets",
                    "user/comfyui-jobs",
                    "user/comfyui-batch",
                    str(kernel_dir),
                    "image-to-video",
                    "gentle camera movement",
                    "",
                    "",
                    str(source_image),
                    512,
                    512,
                    17,
                    node_mappings,
                ))

            downloads = [call for call in calls if call[:2] == ["datasets", "download"]]
            self.assertEqual(len(downloads), 1)
            self.assertTrue(any("Uploading this job" in update for update in updates))
            self.assertTrue(any("Pushing the private GPU kernel" in update for update in updates))
            self.assertIn("image-to-video", updates[-1])
            self.assertEqual(json.loads((kernel_dir / "kernel-metadata.json").read_text())["dataset_sources"], [])

    def test_download_outputs_rejects_empty_successful_kernel_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(mobile_app.Path, "home", return_value=Path(temp_dir)),
                patch.object(mobile_app, "run_kaggle", return_value="Kernel output downloaded"),
            ):
                with self.assertRaisesRegex(RuntimeError, "no supported output files"):
                    list(mobile_app.download_outputs("user/comfyui-batch"))

    def test_download_outputs_reports_saved_media_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            def fake_run_kaggle(args, timeout=600):
                output_dir = Path(args[args.index("-p") + 1])
                (output_dir / "generated.mp4").write_bytes(b"video")
                (output_dir / "preview.png").write_bytes(b"image")
                return "Kernel output downloaded"

            with (
                patch.object(mobile_app.Path, "home", return_value=Path(temp_dir)),
                patch.object(mobile_app, "run_kaggle", side_effect=fake_run_kaggle),
            ):
                updates = list(mobile_app.download_outputs("user/comfyui-batch"))

            self.assertIn("Downloading generated files", updates[0])
            status, images, files = updates[-1]
            self.assertIn("Saved to:", status)
            self.assertEqual(len(images), 1)
            self.assertEqual(len(files), 2)


if __name__ == "__main__":
    unittest.main()
