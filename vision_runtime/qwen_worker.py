"""Offline Qwen3-VL-4B candidate with the existing inspection contract.

The benchmark selects this Engine explicitly. Importing it neither loads weights
nor changes the active production model. Prompts and strict output validation
are shared with the InternVL worker for a controlled comparison.
"""
import json
from pathlib import Path
import threading
import time

from . import worker
from .qwen_download import REPOSITORY as MODEL, REVISION

SYSTEM_PROMPT = worker.SYSTEM_PROMPT
PROMPT = worker.PROMPT
MIN_PIXELS = 65_536
PHOTO_MAX_PIXELS = 1_048_576
VIDEO_MAX_PIXELS = 786_432


class Engine:
    model_id = MODEL
    revision = REVISION

    def __init__(self, directory):
        import torch
        from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

        directory = Path(directory)
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA GPU is required; refusing silent CPU fallback')
        manifest = json.loads((directory / 'cyber102-manifest.json').read_text(encoding='utf-8'))
        if manifest.get('repository') != MODEL or manifest.get('revision') != REVISION:
            raise ValueError('Qwen snapshot does not match the pinned release')
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            directory, local_files_only=True, trust_remote_code=False, use_safetensors=True,
            torch_dtype=torch.bfloat16, attn_implementation='sdpa').eval().to('cuda')
        self.lock = threading.Lock()
        self.device = torch.cuda.get_device_name(0)
        self.prompt = self._prompt([{'type': 'image'}, {'type': 'text', 'text': PROMPT}])
        self.compact_prompt = self._prompt([{'type': 'image'}, {'type': 'text', 'text': worker.COMPACT_PROMPT}])
        self.last_metrics = None
        self.last_memory_metrics = None
        self.last_input_metadata = None
        self.last_output = None

    def _prompt(self, content):
        return self.processor.apply_chat_template([
            {'role': 'system', 'content': SYSTEM_PROMPT}, {'role': 'user', 'content': content}],
            tokenize=False, add_generation_prompt=True)

    def _run(self, images, prompt, *, max_pixels, max_new_tokens, max_time, parse, frame_count=None):
        self.last_metrics = self.last_memory_metrics = self.last_input_metadata = self.last_output = None
        cuda = self.torch.cuda
        cuda.synchronize()
        cuda.reset_peak_memory_stats()
        allocated_before = cuda.memory_allocated()
        started = time.perf_counter()
        try:
            # Qwen preserves aspect ratio and rounds to its native 32-pixel grid.
            # The official processor's shortest/longest edge values are pixel
            # areas, not edge lengths. Bound them explicitly for every request.
            inputs = self.processor(text=prompt, images=images, return_tensors='pt',
                images_kwargs={'size': {'shortest_edge': MIN_PIXELS, 'longest_edge': max_pixels}}).to(
                    'cuda', dtype=self.torch.bfloat16)
            cuda.synchronize()
            prepared = time.perf_counter()
            grid = inputs['image_grid_thw']
            grid_values = grid.tolist()
            grid_cells = sum(t * h * w for t, h, w in grid_values)
            self.last_input_metadata = {
                'pixelValuesShape': list(inputs['pixel_values'].shape),
                'imageGridTHW': grid_values, 'minPixels': MIN_PIXELS, 'maxPixels': max_pixels,
                'nativeGridCells': grid_cells,
                'visionTokens': grid_cells // (int(self.processor.image_processor.merge_size) ** 2),
            }
            with self.torch.inference_mode():
                tokens = self.model.generate(**inputs, do_sample=False, max_new_tokens=max_new_tokens,
                                             max_time=max_time, use_cache=True)
            cuda.synchronize()
            generated_at = time.perf_counter()
            generated = tokens[0, inputs['input_ids'].shape[1]:]
            self.last_output = self.processor.decode(generated, skip_special_tokens=True)
            result = parse(self.last_output)
            finished = time.perf_counter()
            self.last_metrics = {
                'preprocessMs': round((prepared - started) * 1000, 3),
                'generateMs': round((generated_at - prepared) * 1000, 3),
                'decodeMs': round((finished - generated_at) * 1000, 3),
                'totalMs': round((finished - started) * 1000, 3),
                'inputTokens': int(inputs['input_ids'].shape[1]),
                'outputTokens': int(generated.shape[0]),
                # Native 16-pixel vision grid cells; not InternVL's 448px tiles.
                'patches': grid_cells,
            }
            if frame_count is not None:
                self.last_metrics['frames'] = frame_count
            return result
        finally:
            self.last_memory_metrics = {
                'allocatedBeforeMiB': round(allocated_before / 2**20, 3),
                'allocatedAfterMiB': round(cuda.memory_allocated() / 2**20, 3),
                'reservedAfterMiB': round(cuda.memory_reserved() / 2**20, 3),
                'peakAllocatedMiB': round(cuda.max_memory_allocated() / 2**20, 3),
                'peakReservedMiB': round(cuda.max_memory_reserved() / 2**20, 3),
            }

    def infer(self, image, profile='standard'):
        if not isinstance(profile, str) or profile not in worker.PROFILES:
            raise ValueError('Unsupported inference profile')
        return self._run(image, self.compact_prompt if profile == 'fast' else self.prompt,
            max_pixels=PHOTO_MAX_PIXELS, max_new_tokens=180, max_time=85,
            parse=worker.parse_compact if profile == 'fast' else worker.parse_observation)

    def infer_video(self, images, timestamps):
        if not 1 <= len(images) <= worker.MAX_VIDEO_FRAMES or len(images) != len(timestamps):
            raise ValueError('Invalid sampled frame count')
        content = []
        for index, timestamp in enumerate(timestamps):
            content.extend([{'type': 'text', 'text': f'Frame {index}, time {timestamp:g} seconds:\n'},
                            {'type': 'image'}])
        # Identical wording to the established InternVL multi-image contract.
        frame_contract = (
            f'\nThere are EXACTLY {len(images)} input images. Return EXACTLY {len(images)} observations. '
            f'The ONLY allowed frameIndex values, in order, are {list(range(len(images)))}. '
            'The time values are seconds, NOT frame indices or a count of images. '
            'Never create observations for unsupplied frames or intermediate seconds.')
        content.append({'type': 'text', 'text': worker.VIDEO_PROMPT + frame_contract})
        return self._run(images, self._prompt(content), max_pixels=VIDEO_MAX_PIXELS,
            max_new_tokens=worker.VIDEO_MAX_NEW_TOKENS, max_time=110,
            parse=lambda text: worker.parse_video_observations(text, timestamps), frame_count=len(images))

    def warmup(self):
        from PIL import Image
        with Image.new('RGB', (640, 480), 'white') as image:
            self.infer(image)
        return self.last_metrics
