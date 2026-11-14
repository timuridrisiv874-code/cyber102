import unittest
from unittest.mock import patch

import numpy as np

from server import mesh_annotations


class MeshAnnotationsTest(unittest.TestCase):
    def test_model_boxes_need_second_confirmation(self):
        proposal = '''```json
        [{"kind":"corrosion","part":"tank","box":[200,500,700,720]},
         {"kind":"coating_damage","part":"motor","box":[100,300,400,500]}]
        ```'''
        with patch.object(mesh_annotations, 'native_completion',
                          side_effect=[proposal, '<answer>[true,false]</answer>']) as completion:
            accepted = mesh_annotations.detect('unused.jpg', '003.jpg', .67)
        self.assertIn('"part": "tank"', completion.call_args_list[1].args[1])
        self.assertEqual(accepted, [{'kind': 'corrosion', 'part': 'tank',
                                     'box': [200, 500, 700, 720], 'frame': '003.jpg', 'time': .67}])

    def test_image_point_uses_nearest_triangle_and_stays_on_mesh(self):
        front = np.array([[-.5, .5, -1], [.5, .5, -1], [0, -.5, -1]], dtype=np.float32)
        back = front * 2
        raw = np.concatenate((front, back))
        positions = np.array([[-.8, -.8, -.5], [.8, -.8, -.5], [0, .8, -.5],
                              [-.8, -.8, .5], [.8, -.8, .5], [0, .8, .5]], dtype=np.float32)
        faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
        cameras = {
            'views': [{'path': '003.jpg', 'poseId': '1', 'intrinsicId': '2',
                       'width': '100', 'height': '100'}],
            'poses': [{'poseId': '1', 'pose': {'transform': {
                'rotation': ['1', '0', '0', '0', '1', '0', '0', '0', '1'],
                'center': ['0', '0', '0']}}}],
            'intrinsics': [{'intrinsicId': '2', 'focalLength': '36', 'sensorWidth': '36',
                            'principalPoint': ['0', '0']}],
        }
        detected = [{'kind': 'corrosion', 'part': 'tank', 'box': [400, 400, 600, 600],
                     'frame': '003.jpg', 'time': .67}]
        marks = mesh_annotations.project_detections(raw, positions, faces, cameras, detected)
        self.assertEqual(len(marks), 1)
        np.testing.assert_allclose(marks[0]['point'], [0, 0, -.5], atol=.00001)
        self.assertEqual(marks[0]['time'], .67)


if __name__ == '__main__':
    unittest.main()
