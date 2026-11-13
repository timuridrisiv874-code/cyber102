from email import policy
from email.parser import BytesParser
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from server.vision_models import model_spec
from vision_runtime import benchmark_crop as crop


SPEC = model_spec('qwen3_vl_4b')
PROTOCOL = {'model':SPEC.model_id, 'revision':SPEC.revision}
CAPS = {'vision':{'enabled':True, 'queue':{'enabled':True}, 'profiles':['standard'], **PROTOCOL}}
RESULT = {'source':'local_vision', 'model':SPEC.model_id, 'profile':'standard', 'status':'defect',
          'component':'Корпус насоса', 'box':{'x':.25, 'y':.25, 'width':.5, 'height':.5},
          'metrics':{'patches':2048, 'inputTokens':620}}


class QueueFixture:
    def __init__(self, mode='success'):
        self.mode, self.calls, self.now = mode, [], 0

    def transport(self, method, path, body=None, headers=None, timeout=20):
        self.calls.append((method, path, body, headers, timeout))
        if path == '/api/capabilities':
            return 200, CAPS
        if path.endswith('/cancel'):
            return 200, {'job':{'status':'cancelling'}}
        if path == '/analyze':
            if self.mode == 'upload_lost':
                raise OSError('Acceptance response lost')
            if self.mode == 'full':
                return 503, {'code':'analysis_queue_full'}
            return 202, {'job':{'id':'a'*32, 'type':'photo', 'status':'queued', 'position':1}}
        if self.mode == 'interrupted':
            raise KeyboardInterrupt()
        return 200, {'job':{'id':'b'*32 if self.mode == 'wrong_id' else 'a'*32, 'type':'photo',
                            'status':'succeeded', 'position':None, 'result':RESULT}}

    def sleep(self, seconds):
        self.now += 800 if self.mode == 'timeout' else seconds

    def client(self):
        return crop.QueueClient(self.transport, PROTOCOL, clock=lambda:self.now, sleep=self.sleep)


class CropTests(unittest.TestCase):
    def test_original_geometry_padding_clipping_and_round_trip(self):
        plan = crop.crop_plan(RESULT, (1000, 800))
        self.assertEqual(plan['pixels'], [200, 160, 800, 640])
        self.assertTrue(plan['eligible'])
        mapped = crop.map_crop_box({'x':0, 'y':0, 'width':1, 'height':1}, plan, (1000, 800))
        self.assertEqual(mapped, {'x':.2, 'y':.2, 'width':.6, 'height':.6})
        edge = crop.crop_plan({**RESULT, 'box':{'x':0, 'y':0, 'width':.2, 'height':.25}}, (1000,800))
        self.assertEqual(edge['pixels'][:2], [0,0])
        self.assertLessEqual(edge['pixels'][2], 221)
        self.assertLessEqual(edge['pixels'][3], 221)

    def test_abstention_invalid_and_unhelpful_crops_are_skipped(self):
        self.assertEqual(crop.crop_plan({'status':'unusable'}, (1000,800))['reason'], 'no_confident_component_box')
        self.assertFalse(crop.crop_plan({**RESULT, 'condition':'uncertain'}, (1000,800))['eligible'])
        for bad in [None, [0,0,1,1], {'x':True,'y':0,'width':.1,'height':.1},
                    {'x':0,'y':0,'width':float('nan'),'height':.1}, {'x':.9,'y':0,'width':.2,'height':.1}]:
            self.assertEqual(crop.crop_plan({**RESULT,'box':bad}, (1000,800))['reason'], 'invalid_box')
        full = crop.crop_plan({**RESULT,'box':{'x':0,'y':0,'width':1,'height':1}}, (1000,800))
        self.assertEqual(full['reason'], 'crop_covers_almost_full_image')
        self.assertEqual(crop.crop_plan(RESULT, (100,100))['reason'], 'crop_too_small')

    def test_pixel_gain_distinguishes_source_retention_from_context_reduction(self):
        plan = crop.crop_plan(RESULT, (1600,1200))
        metrics = crop.pixel_metrics((1600,1200), {'width':1280,'height':960}, plan,
                                     {'width':plan['width'],'height':plan['height']})
        self.assertAlmostEqual(metrics['sourceResolutionRetentionGainX'], 1.25)
        small = crop.crop_plan(RESULT, (600,800))
        metrics = crop.pixel_metrics((600,800), {'width':600,'height':800}, small,
                                     {'width':small['width'],'height':small['height']})
        self.assertEqual(metrics['sourceResolutionRetentionAreaGain'], 1)
        self.assertLess(metrics['retainedContextAreaFraction'], 1)
        self.assertIsNone(crop.input_grid(RESULT)['imageGridTHW'])
        self.assertEqual(crop.input_grid(RESULT)['nativeGridCells'], 2048)

    def test_exif_rotation_precedes_normalization_and_no_artificial_upscale(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'portrait.jpg'
            image = Image.new('RGB', (100,200), 'red')
            exif = Image.Exif(); exif[274] = 6
            image.save(path, exif=exif)
            with crop.open_original(path, crop.digest(path.read_bytes())) as original:
                self.assertEqual(original.size, (200,100))
                raw, meta = crop.normalized_jpeg(original)
                self.assertEqual((meta['width'], meta['height']), (200,100))
                with Image.open(BytesIO(raw)) as normalized:
                    self.assertEqual(normalized.mode, 'RGB')
                    self.assertIsNone(normalized.getexif().get(274))

    def test_multipart_matches_public_upload_contract(self):
        raw, _ = crop.normalized_jpeg(Image.new('RGB', (100,80)))
        body, headers = crop.multipart(raw)
        parsed = BytesParser(policy=policy.default).parsebytes(('Content-Type: '+headers['Content-Type']+'\r\n\r\n').encode()+body)
        fields = {part.get_param('name',header='content-disposition'):part.get_payload(decode=True) for part in parsed.iter_parts()}
        self.assertEqual(fields, {'file':raw,'profile':b'standard','captureSource':b'uploaded_photo'})
        from server.vision import extract_request
        image, profile, metadata = extract_request(headers['Content-Type'], body, include_metadata=True)
        self.assertEqual(profile, 'standard')
        self.assertEqual(metadata['captureSource'], 'uploaded_photo')
        self.assertEqual(Image.open(BytesIO(image)).size, (100,80))

    def test_queue_uses_one_upload_and_preserves_same_client_for_poll(self):
        fixture = QueueFixture()
        record = fixture.client().analyze(b'jpeg')
        self.assertTrue(record['success'])
        self.assertEqual(record['result'], RESULT)
        posts = [call for call in fixture.calls if call[0]=='POST']
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][1], '/analyze')
        self.assertEqual(posts[0][3]['Prefer'], 'respond-async')
        polls = [call for call in fixture.calls if call[1].startswith('/api/analysis-jobs/')]
        self.assertEqual(polls[0][3]['X-Analysis-Client'], posts[0][3]['X-Analysis-Client'])
        self.assertEqual([call[1] for call in fixture.calls].count('/api/capabilities'), 2)

    def test_failed_upload_timeout_wrong_job_and_interrupt_cancel_without_resubmission(self):
        for mode in ['upload_lost','timeout','wrong_id','interrupted','full']:
            fixture = QueueFixture(mode)
            with self.assertRaises(KeyboardInterrupt if mode=='interrupted' else crop.QueueFailure):
                fixture.client().analyze(b'jpeg')
            self.assertEqual(len([call for call in fixture.calls if call[1]=='/analyze']), 1)
            cancels = [call for call in fixture.calls if call[1].endswith('/cancel')]
            self.assertEqual(len(cancels), 0 if mode=='full' else 1)
            if cancels:
                self.assertIn('/by-request/', cancels[0][1])
                self.assertEqual(cancels[0][2], b'')

    def test_wrong_capabilities_block_all_post_and_result_model_mismatch_is_rejected(self):
        calls = []
        def wrong(method, path, *args, **kwargs):
            calls.append(method)
            return 200, {'vision':{**CAPS['vision'],'revision':'other'}}
        with self.assertRaises(ValueError):
            crop.QueueClient(wrong, PROTOCOL).analyze(b'jpeg')
        self.assertEqual(calls, ['GET'])
        with self.assertRaises(ValueError):
            crop.verify_result({**RESULT,'model':'other'}, PROTOCOL)
        for endpoint in ['https://example.com', 'http://94.41.87.102:8102', 'http://user@127.0.0.1:8102', 'http://127.0.0.1:8102/analyze']:
            with self.assertRaises(ValueError):
                crop.loopback_endpoint(endpoint)

    def test_prepare_freezes_exact_panel_without_network_or_copying_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); sources = root/'sources'; sources.mkdir()
            entries = []
            for index in crop.PANEL:
                path = sources/f'{index}.jpg'
                Image.new('RGB',(100,80),(index,0,0)).save(path)
                entries.append({'index':index,'id':crop.digest(path.read_bytes()),'catalog_source_path':path.name,
                                'oriented_original_dimensions':[100,80]})
            inventory = root/'inventory.json'; inventory.write_text(json.dumps({'images':entries}))
            output = root/'prepared'
            with patch.object(crop, 'LocalHTTP', side_effect=AssertionError('Preparation must be offline')):
                digest = crop.prepare(inventory,sources,output,'http://127.0.0.1:8102')
            self.assertEqual(digest,crop.digest((output/'protocol.json').read_bytes()))
            self.assertEqual(len(list((output/'inputs').iterdir())),12)
            self.assertEqual(len(list(output.iterdir())),2,'No source copies, model files or inference outputs')
            protocol = json.loads((output/'protocol.json').read_text())
            self.assertEqual(protocol['panelOneBased'],crop.PANEL)
            self.assertEqual(protocol['revision'],SPEC.revision)
            with self.assertRaisesRegex(ValueError,'Protocol SHA'):
                crop.run(output,sources,'0'*64)
            self.assertFalse((output/'.run-started').exists())
            with patch.object(crop, 'source_hashes', return_value={'changed':'source'}):
                with self.assertRaisesRegex(ValueError,'changed after preparation'):
                    crop.run(output,sources,digest)
            self.assertFalse((output/'.run-started').exists())
            class Client:
                def __init__(self, *_args):
                    pass
                def guard(self):
                    return CAPS['vision']
                def analyze(self, _jpeg):
                    return {'success':True, 'result':RESULT, 'elapsedSeconds':1}
            # Make this small fixture eligible, then fail crop encoding after A.
            with patch.object(crop,'QueueClient',Client), patch.object(crop,'crop_plan',return_value={
                    'eligible':True,'pixels':[0,0,80,80],'width':80,'height':80,'areaFraction':.8}), \
                    patch.object(crop,'normalized_jpeg',side_effect=OSError('crop encode failed')):
                self.assertEqual(crop.run(output,sources,digest),2)
            report = json.loads((output/'results.json').read_text())
            self.assertTrue(report['records'][0]['A']['success'])
            self.assertEqual(report['records'][0]['A']['result'],RESULT)
            self.assertEqual(report['records'][0]['cropPreparation']['errorType'],'OSError')
            with self.assertRaises(FileExistsError):
                crop.prepare(inventory,sources,output,'http://127.0.0.1:8102')


if __name__ == '__main__':
    unittest.main()
