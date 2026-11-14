"""Local, single-worker video reconstruction service for the inspection UI."""
import argparse
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import trimesh

if __package__:
    from . import mesh_annotations
else:
    import mesh_annotations

UPLOAD_LIMIT = 40 * 1024 * 1024
RETENTION = 7200
ROUTE = '/api/mesh-jobs'
JOB_PATH = re.compile(r'/api/mesh-jobs/([0-9a-f]{40})(/mesh)?')
FRAME_PROFILES = {'fast': (4, 20), 'detailed': (8, 42)}


def frame_plan(duration, profile):
    rate, limit = FRAME_PROFILES[profile]
    return min(rate, limit / duration), limit


def run(command, log, timeout, *, env=None):
    with log.open('wb') as output:
        process = subprocess.Popen([str(part) for part in command], stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, env=env)
        try:
            code = process.wait(timeout)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            if process.poll() is None:
                process.kill()
            process.wait()
            raise ValueError('Построение заняло слишком много времени. Попробуйте ролик покороче.') from None
    if code:
        raise ValueError('Не удалось обработать видео. Проверьте файл и ракурсы съёмки.')


def export_mesh(work, ffmpeg, detections=()):
    result = work / 'publish'
    obj = result / 'texturedMesh.obj'
    mtl = result / 'texturedMesh.mtl'
    if not obj.is_file() or not mtl.is_file():
        raise ValueError('На видео не хватило общих деталей между ракурсами для построения 3D.')
    # AliceVision exports linear EXR. Convert every atlas to sRGB before sampling vertex colors.
    materials = mtl.read_text(encoding='utf-8')
    for texture in result.glob('texture_*.exr'):
        target = texture.with_suffix('.png')
        run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-i', texture, '-vf',
             "scale='min(iw,4096)':-1,format=rgb24,lutrgb=r=gammaval(0.4545):g=gammaval(0.4545):b=gammaval(0.4545)",
             '-frames:v', '1', target], work / 'texture.log', 60)
        materials = materials.replace(texture.name, target.name)
    mtl.write_text(materials, encoding='utf-8')
    scene = trimesh.load_scene(obj, process=False)
    parts = []
    for mesh in scene.geometry.values():
        if not len(mesh.faces):
            continue
        if mesh.visual.kind == 'texture':
            mesh.visual = mesh.visual.to_color()
        parts.append(mesh)
    if not parts:
        raise ValueError('Meshroom не нашёл поверхность на этом видео.')
    mesh = trimesh.util.concatenate(parts)
    if len(mesh.faces) < 100 or len(mesh.faces) > 1_000_000:
        raise ValueError('Модель не подходит для просмотра. Попробуйте короче снять один объект.')
    raw_xyz = np.asarray(mesh.vertices, dtype=np.float32)
    xyz = raw_xyz.copy()
    center = np.median(xyz, axis=0)
    radius = np.quantile(np.linalg.norm(xyz - center, axis=1), .95)
    # Frame the subject seen near the center of multiple shots instead of the full floor.
    camera_file = next((work / 'cache' / 'StructureFromMotion').glob('*/cameras.sfm'), None)
    data = None
    if camera_file:
        data = json.loads(camera_file.read_text(encoding='utf-8'))
        poses = {item['poseId']: item['pose']['transform'] for item in data['poses']}
        cameras = [poses[view['poseId']] for view in sorted(data['views'], key=lambda item: item['path'])
                   if view['poseId'] in poses]
        if cameras:
            xyz = xyz * np.array([1, -1, -1], dtype=np.float32)
            samples, distances = [], []
            for pose in cameras[::max(1, len(cameras) // 10)]:
                rotation = np.asarray(pose['rotation'], dtype=np.float32).reshape(3, 3)
                camera = np.asarray(pose['center'], dtype=np.float32)
                local = (xyz - camera) @ rotation
                x, y, z = local.T
                inside = (z > 0) & (abs(x) < .09 * z) & (abs(y - .045 * z) < .12 * z)
                if np.count_nonzero(inside) < 80:
                    continue
                depth = np.quantile(z[inside], .30)
                near = inside & (z <= depth)
                samples.append(np.median(xyz[near], axis=0))
                distances.append(depth)
            center = np.median(xyz, axis=0)
            if len(samples) >= 3:
                focus = np.median(samples, axis=0)
                focus_radius = float(np.median(distances) * .60)
                if np.median(np.linalg.norm(samples - focus, axis=1)) < .60 * focus_radius:
                    center, radius = focus, min(radius, focus_radius)
            rotation = np.asarray(cameras[0]['rotation'], dtype=np.float32).reshape(3, 3)
            xyz = (xyz - center) @ rotation
            center = np.zeros(3, dtype=np.float32)
    if not np.isfinite(xyz).all() or not radius > 0:
        raise ValueError('Meshroom вернул некорректную геометрию.')
    positions = ((xyz - center) / radius).astype('<f4')
    faces = mesh.faces.astype('<u4')
    colors = mesh.visual.vertex_colors[:, :3].astype('uint8')
    with (work / 'mesh.bin').open('wb') as output:
        for array in (positions, faces, colors):
            output.write(array.tobytes())
    auto_marks = []
    if data and detections:
        try:
            auto_marks = mesh_annotations.project_detections(raw_xyz, positions, mesh.faces, data, detections)
        except (KeyError, ValueError, TypeError, IndexError, FloatingPointError):
            logging.exception('Could not project model findings onto 3D mesh')
    return {'vertices': len(positions), 'triangles': len(faces), 'indexBytes': 4,
            'autoMarks': auto_marks}


class Jobs:
    def __init__(self, root, ffmpeg, meshroom):
        self.root, self.ffmpeg, self.meshroom = root, ffmpeg, meshroom
        self.lock = threading.Lock()
        self.active = threading.BoundedSemaphore(1)
        self.jobs = {}
        root.mkdir(parents=True, exist_ok=True)
        self.cleanup()
        for status in self.root.glob('*/result.json'):
            if not JOB_PATH.fullmatch(ROUTE + '/' + status.parent.name):
                continue
            try:
                job = json.loads(status.read_text(encoding='utf-8'))
                if job['id'] == status.parent.name and job['state'] == 'ready':
                    self.jobs[job['id']] = job
            except (OSError, ValueError, KeyError):
                logging.exception('Could not restore mesh metadata from %s', status)

    def cleanup(self):
        now = time.time()
        with self.lock:
            for path in self.root.iterdir():
                if not re.fullmatch(r'[0-9a-f]{40}', path.name) or not path.is_dir():
                    continue
                job = self.jobs.get(path.name)
                if job and job['state'] in ('queued', 'frames', 'building', 'exporting'):
                    continue
                if now - path.stat().st_mtime > RETENTION:
                    shutil.rmtree(path, ignore_errors=True)
                    self.jobs.pop(path.name, None)

    def set(self, token, state, **fields):
        job = {'id': token, 'state': state, **fields}
        if state == 'ready':
            (self.root / token / 'result.json').write_text(json.dumps(job), encoding='utf-8')
        with self.lock:
            self.jobs[token] = job

    def get(self, token):
        with self.lock:
            return self.jobs.get(token)

    def build(self, token, work, profile='fast'):
        started = time.monotonic()
        try:
            self.set(token, 'frames', profile=profile)
            (work / 'frames').mkdir()
            (work / 'publish').mkdir()
            ffprobe = self.ffmpeg.with_name('ffprobe' + self.ffmpeg.suffix)
            run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                 'format=duration:stream=width,height', '-of', 'json', work / 'input.video'],
                work / 'probe.log', 20)
            probe = json.loads((work / 'probe.log').read_text(encoding='utf-8'))
            stream = next(iter(probe.get('streams', [])), {})
            duration = float(probe.get('format', {}).get('duration', 0))
            if not 2 <= duration <= 30 or not 0 < stream.get('width', 0) * stream.get('height', 0) <= 9_000_000:
                raise ValueError('Выберите видео длительностью 2–30 секунд с разрешением до 4K.')
            fps, frame_limit = frame_plan(duration, profile)
            run([self.ffmpeg, '-nostdin', '-v', 'error', '-threads', '2', '-i', work / 'input.video',
                 '-map', '0:v:0', '-an', '-filter_threads', '2', '-vf',
                 f"fps={fps:.6f},scale='min(960,iw)':-2", '-frames:v', str(frame_limit),
                 '-q:v', '2', work / 'frames' / '%03d.jpg'], work / 'frames.log', 90)
            (work / 'input.video').unlink()
            frame_count = len(list((work / 'frames').glob('*.jpg')))
            if frame_count < 8:
                raise ValueError('Слишком мало кадров для 3D. Загрузите короткий обход оборудования.')
            self.set(token, 'building', profile=profile, framesUsed=frame_count)
            env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}
            run([self.meshroom, '--input', work / 'frames', '--output', work / 'publish',
                 '--cache', work / 'cache'], work / 'meshroom.log', 1200, env=env)
            self.set(token, 'exporting', profile=profile, framesUsed=frame_count)
            detected = mesh_annotations.detect_samples(work / 'frames', frame_count, fps)
            meta = export_mesh(work, self.ffmpeg, detected)
            self.set(token, 'ready', **meta, profile=profile, framesUsed=frame_count,
                     processingSeconds=round(time.monotonic() - started))
        except (ValueError, OSError, KeyError) as error:
            logging.exception('Mesh job %s failed', token)
            message = str(error) if type(error) is ValueError and not str(error).isascii() else (
                'Не удалось построить 3D-модель из этого видео.')
            self.set(token, 'failed', message=message)
        except Exception:
            logging.exception('Mesh export failed for %s', token)
            self.set(token, 'failed', message='Не удалось открыть результат реконструкции.')
        finally:
            for directory in ('frames', 'cache', 'publish'):
                shutil.rmtree(work / directory, ignore_errors=True)
            (work / 'input.video').unlink(missing_ok=True)
            try:
                os.utime(work)
            finally:
                self.active.release()


class Handler(BaseHTTPRequestHandler):
    timeout = 45

    def reply(self, code, data):
        content = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        if self.path != ROUTE:
            self.reply(404, {'message': 'Маршрут не найден.'})
            return
        origin = self.headers.get_all('Origin', [])
        if origin and origin != [self.server.origin]:
            self.reply(403, {'message': 'Откройте приложение на его основном адресе.'})
            return
        if self.headers.get_content_type() not in ('video/mp4', 'video/quicktime', 'video/webm', 'video/x-m4v', 'application/octet-stream'):
            self.reply(415, {'message': 'Выберите видео MP4, MOV или WebM.'})
            return
        profile = self.headers.get('X-Cyber102-Mesh-Profile', 'fast')
        if profile not in FRAME_PROFILES:
            self.reply(400, {'message': 'Выберите быстрый или подробный режим 3D.'})
            return
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,9}', lengths[0]):
            self.reply(400, {'message': 'Не удалось определить размер видео.'})
            return
        size = int(lengths[0])
        if not 0 < size <= UPLOAD_LIMIT:
            self.reply(413, {'message': 'Выберите видео до 40 МБ.'})
            return
        jobs = self.server.jobs
        if not jobs.active.acquire(blocking=False):
            self.reply(429, {'message': 'На сервере уже строится модель. Повторите чуть позже.'})
            return
        token = secrets.token_hex(20)
        work = jobs.root / token
        handed_off = False
        try:
            jobs.cleanup()
            if shutil.disk_usage(jobs.root).free < 3 * 1024**3:
                self.reply(503, {'message': 'На сервере сейчас недостаточно места для 3D.'})
                return
            work.mkdir()
            expires = time.monotonic() + 90
            with (work / 'input.video').open('wb') as video:
                while size:
                    if time.monotonic() > expires:
                        raise TimeoutError
                    chunk = self.rfile.read1(min(size, 256 * 1024))
                    if not chunk:
                        raise OSError('Incomplete upload')
                    video.write(chunk)
                    size -= len(chunk)
            jobs.set(token, 'queued', profile=profile)
            threading.Thread(target=jobs.build, args=(token, work, profile), daemon=True).start()
            handed_off = True
            self.reply(202, {'id': token, 'state': 'queued'})
        except OSError:
            if not handed_off:
                self.reply(400, {'message': 'Не удалось получить видео целиком.'})
        finally:
            if not handed_off:
                shutil.rmtree(work, ignore_errors=True)
                jobs.active.release()

    def do_GET(self):
        route = JOB_PATH.fullmatch(urlsplit(self.path).path)
        jobs = self.server.jobs
        jobs.cleanup()
        job = jobs.get(route[1]) if route else None
        if not job:
            self.reply(404, {'message': 'Модель уже недоступна. Загрузите видео повторно.'})
        elif not route[2]:
            self.reply(200, job)
        elif job['state'] != 'ready':
            self.reply(409, {'message': 'Модель ещё строится.'})
        else:
            try:
                with (jobs.root / route[1] / 'mesh.bin').open('rb') as data:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(os.fstat(data.fileno()).st_size))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    shutil.copyfileobj(data, self.wfile)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8107)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--ffmpeg', required=True, type=Path)
    parser.add_argument('--meshroom', required=True, type=Path)
    args = parser.parse_args()
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        server.origin = args.origin
        server.jobs = Jobs(args.root, args.ffmpeg, args.meshroom)
        server.serve_forever()


if __name__ == '__main__':
    main()
